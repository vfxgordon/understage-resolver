"""Lore backend, against a throwaway loreserver on free local ports.

Skipped unless `lore` and `loreserver` are found in $LORE_BIN_DIR or on PATH. Tested with Lore v0.10.0.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
from conftest import (
    CONFIG_FILE,
    PROVENANCE_FILE,
    ROBOT,
    SERVO,
    L,
    check_backend_publish,
    composition_errors,
    robot_files,
    servo_files,
)
from pxr import Ar, Usd

import AssetLibResolver


def _find(tool):
    d = os.environ.get("LORE_BIN_DIR")
    if d and (Path(d) / tool).is_file():
        return str(Path(d) / tool)
    return shutil.which(tool)


LORE, LORESERVER = _find("lore"), _find("loreserver")
pytestmark = pytest.mark.skipif(not (LORE and LORESERVER), reason="lore/loreserver not installed")


def _free_port():
    """A port free for both TCP (gRPC, HTTP) and UDP (QUIC)."""
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.bind(("127.0.0.1", 0))
            port = tcp.getsockname()[1]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                try:
                    udp.bind(("127.0.0.1", port))
                except OSError:
                    continue
            return port


@pytest.fixture(scope="session")
def lore_server(tmp_path_factory):
    """One loreserver for the session, isolated from any real Lore setup:
    its own ports, store and temp dir, and client data/config/cache dirs."""
    base = tmp_path_factory.mktemp("lore")
    saved = dict(os.environ)
    for xdg in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        os.environ[xdg] = str(base / xdg.lower())

    public, internal, http = _free_port(), _free_port(), _free_port()
    env = dict(os.environ, TMPDIR=str(base), **{
        "LORE__SERVER__QUIC__HOST": "127.0.0.1", "LORE__SERVER__QUIC__PORT": str(public),
        "LORE__SERVER__GRPC__HOST": "127.0.0.1", "LORE__SERVER__GRPC__PORT": str(public),
        "LORE__SERVER__QUIC_INTERNAL__HOST": "127.0.0.1",
        "LORE__SERVER__QUIC_INTERNAL__PORT": str(internal),
        "LORE__SERVER__GRPC_INTERNAL__HOST": "127.0.0.1",
        "LORE__SERVER__GRPC_INTERNAL__PORT": str(internal),
        "LORE__SERVER__HTTP__HOST": "127.0.0.1", "LORE__SERVER__HTTP__PORT": str(http),
        "LORE__IMMUTABLE_STORE__LOCAL__PATH": str(base / "store"),
        "LORE__MUTABLE_STORE__LOCAL__PATH": str(base / "store"),
    })
    log = open(base / "server.log", "w")
    server = subprocess.Popen([LORESERVER], env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{http}/health_check", timeout=1)
                break
            except OSError:
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f"loreserver did not start; see {base / 'server.log'}")
                time.sleep(0.2)
        yield f"lore://127.0.0.1:{public}"
    finally:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()
        os.environ.clear()
        os.environ.update(saved)


class Repo:
    """A Lore repository on the test server plus a working tree for commits."""

    def __init__(self, server, tmp_path):
        self.url = f"{server}/assets-{uuid.uuid4().hex[:8]}"
        self.ws = tmp_path / "ws"
        self.ws.mkdir()
        self.lore("repository", "create", self.url, "--identity", "tester@example.com")

    def lore(self, *args):
        return subprocess.run([LORE, "--non-interactive", "--no-pager", *args], cwd=self.ws,
                              capture_output=True, text=True, check=True).stdout

    def commit(self, asset, files, message):
        """Write files under <asset>/ (no version folders), commit and push."""
        paths = []
        for name, text in files.items():
            path = self.ws / asset / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            paths.append(str(path.relative_to(self.ws)))
        self.lore("stage", *paths)  # explicit paths: staging a dir misses edits
        self.lore("commit", message)
        self.lore("push")


@pytest.fixture
def repo(lore_server, tmp_path):
    """History (repository revision -> asset version):

    1  servo v1
    2  franka v1   (refs servo ?v=1)
    3  franka v2   (geo edited only; still refs servo ?v=1)
    4  servo v2
    """
    r = Repo(lore_server, tmp_path)
    r.commit(SERVO, servo_files(1), "servo v1")
    r.commit(ROBOT, robot_files(1, f"{L}{SERVO}?v=1"), "franka v1")
    r.commit(ROBOT, {"geo.usda": robot_files(2, "")["geo.usda"]}, "franka v2: geo")
    r.commit(SERVO, servo_files(2), "servo v2")
    return r


@pytest.fixture
def cache(repo, tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    (root / CONFIG_FILE).write_text(json.dumps({"backend": {"command": [
        sys.executable, "-m", "assetlib_backends.lore", "--lore", LORE,
        "--url", repo.url, "--instance", str(tmp_path / "instance"),
    ]}}))
    return root


def context(root):
    return Ar.ResolverContext(AssetLibResolver.ResolverContext.FromPins({}, str(root)))


def resolve(root, uri):
    with Ar.ResolverContextBinder(context(root)):
        return Ar.GetResolver().Resolve(uri).GetPathString()


def test_versions_are_nth_change_to_the_asset(cache):
    # franka was changed by revisions 2 and 3 -> versions 1, 2.
    assert resolve(cache, f"{L}{ROBOT}") == str(cache / ROBOT / "v002" / "franka.usda")
    source = json.loads((cache / ROBOT / "v002" / PROVENANCE_FILE).read_text())["source"]
    assert f"/{ROBOT}@3 (" in source


def test_each_version_is_the_asset_as_of_its_change(cache):
    v1 = Usd.Stage.Open(f"{L}{ROBOT}?v=1", context(cache))
    v2 = Usd.Stage.Open(f"{L}{ROBOT}?v=2", context(cache))
    assert v1.GetPrimAtPath("/franka/body").GetAttribute("size").Get() == 1
    assert v2.GetPrimAtPath("/franka/body").GetAttribute("size").Get() == 2
    # v2 includes the entry layer, unchanged since v1.
    assert (cache / ROBOT / "v002" / "franka.usda").is_file()


def test_scene_composes_with_pinned_dependency(cache):
    stage = Usd.Stage.Open(f"{L}{ROBOT}?v=2", context(cache))
    # franka v2 still references servo v1, although servo v2 exists.
    assert stage.GetPrimAtPath("/franka/joint1_servo").GetAttribute("pipeline:version").Get() == 1
    assert not composition_errors(stage)
    assert not (cache / SERVO / "v002").exists()


def test_nested_files_and_spaces(repo, cache):
    repo.commit(ROBOT, {"meshes/hi/link 2.usdc": "m2", "meshes/link1.usdc": "m1"}, "meshes")
    vdir = Path(resolve(cache, f"{L}{ROBOT}?v=3")).parent
    assert (vdir / "meshes" / "hi" / "link 2.usdc").read_text() == "m2"
    assert (vdir / "meshes" / "link1.usdc").read_text() == "m1"
    assert (vdir / "geo.usda").is_file()


def test_missing_version(cache, capfd):
    assert resolve(cache, f"{L}{ROBOT}?v=9") == ""
    assert "no such version on the remote" in capfd.readouterr().err


def test_unknown_asset_has_no_versions(cache):
    assert resolve(cache, f"{L}props/nothing") == ""


def test_instance_stays_bare(cache, tmp_path):
    resolve(cache, f"{L}{ROBOT}?v=1")
    assert [p.name for p in (tmp_path / "instance").iterdir()] == [".lore"]


def test_parallel_prefetch_shares_one_instance(cache):
    """Several helpers at once, including racing to create the instance."""
    uris = [f"{L}{a}?v={n}" for a in (ROBOT, SERVO) for n in (1, 2)]
    with Ar.ResolverContextBinder(context(cache)):
        result = AssetLibResolver.Prefetch(uris, jobs=4)
    assert all(result.values()), result


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_fetched_files_are_read_only(cache):
    entry = Path(resolve(cache, f"{L}{SERVO}?v=2"))
    with pytest.raises(PermissionError):
        entry.write_text("oops")


def test_publish(repo, cache, tmp_path):
    check_backend_publish(cache, tmp_path / "src")


def test_publish_without_changes_is_refused(repo, cache, tmp_path):
    from conftest import backend_publish

    (tmp_path / "src").mkdir()
    r = backend_publish(cache, SERVO, 3, servo_files(2), tmp_path / "src")
    assert r.returncode == 1 and "nothing changed since version 2" in r.stderr
