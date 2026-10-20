"""Perforce backend, against a throwaway p4d started on demand (rsh: port).

Skipped unless p4 and p4d are found in $P4_BIN_DIR or on PATH.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import (
    CONFIG_FILE,
    PROVENANCE_FILE,
    ROBOT,
    SERVO,
    L,
    composition_errors,
    robot_files,
    servo_files,
)
from pxr import Ar, Usd

import AssetLibResolver


def _find(tool):
    d = os.environ.get("P4_BIN_DIR")
    if d and (Path(d) / tool).is_file():
        return str(Path(d) / tool)
    return shutil.which(tool)


P4, P4D = _find("p4"), _find("p4d")
pytestmark = pytest.mark.skipif(not (P4 and P4D), reason="p4/p4d not installed")

DEPOT_ROOT = "//depot/assets"


class Depot:
    """A p4d server plus a client workspace for submitting test content."""

    def __init__(self, tmp_path, monkeypatch):
        self.ws = tmp_path / "ws"
        self.ws.mkdir()
        server = tmp_path / "p4root"
        server.mkdir()
        # Inherited by the backend helper the resolver spawns, too.
        for var, value in {
            "P4PORT": f"rsh:{P4D} -r {server} -L log -i -J off",
            "P4USER": "tester",
            "P4CLIENT": "tester-ws",
            "P4TICKETS": str(tmp_path / "p4tickets"),
            "P4ENVIRO": os.devnull,
            "P4CONFIG": "",
        }.items():
            monkeypatch.setenv(var, value)
        spec = self.p4("client", "-o")
        spec = "\n".join(
            f"Root:\t{self.ws}" if line.startswith("Root:") else line
            for line in spec.splitlines())
        self.p4("client", "-i", input=spec)

    def p4(self, *args, input=None):
        return subprocess.run([P4, *args], input=input, capture_output=True, text=True,
                              check=True, cwd=self.ws).stdout

    def submit(self, asset, files, description):
        """Write files under assets/<asset>/ (no version folders) and submit."""
        adir = self.ws / "assets" / asset
        adir.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            path = adir / name
            if path.exists():
                self.p4("edit", str(path))  # submitted files are read-only
            path.write_text(text)
        self.p4("reconcile", "-a", "-e", "-d", f"{self.ws}/...")
        out = self.p4("submit", "-d", description)
        return int(out.strip().splitlines()[-1].split()[1])  # "Change N submitted."


@pytest.fixture
def depot(tmp_path, monkeypatch):
    """History (changelist -> asset version):

    1  servo v1
    2  franka v1   (refs servo ?v=1)
    3  franka v2   (geo edited only; still refs servo ?v=1)
    4  servo v2
    """
    d = Depot(tmp_path, monkeypatch)
    d.submit(SERVO, servo_files(1), "servo v1")
    d.submit(ROBOT, robot_files(1, f"{L}{SERVO}?v=1"), "franka v1")
    d.submit(ROBOT, {"geo.usda": robot_files(2, "")["geo.usda"]}, "franka v2: geo")
    d.submit(SERVO, servo_files(2), "servo v2")
    return d


@pytest.fixture
def cache(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    (root / CONFIG_FILE).write_text(json.dumps({"backend": {"command": [
        sys.executable, "-m", "assetlib_backends.p4", "--p4", P4, "--depot-root", DEPOT_ROOT,
    ]}}))
    return root


def context(root):
    return Ar.ResolverContext(AssetLibResolver.ResolverContext.FromPins({}, str(root)))


def resolve(root, uri):
    with Ar.ResolverContextBinder(context(root)):
        return Ar.GetResolver().Resolve(uri).GetPathString()


def test_depot_has_no_version_folders(depot):
    files = depot.p4("files", f"{DEPOT_ROOT}/...").splitlines()
    assert files and not any("/v0" in f for f in files)


def test_versions_are_nth_change_to_the_asset(depot, cache):
    # franka was touched by changes 2 and 3 -> versions 1, 2.
    assert resolve(cache, f"{L}{ROBOT}") == str(cache / ROBOT / "v002" / "franka.usda")
    source = json.loads((cache / ROBOT / "v002" / PROVENANCE_FILE).read_text())["source"]
    assert source == f"{DEPOT_ROOT}/{ROBOT}/...@3"


def test_each_version_is_the_asset_as_of_its_change(depot, cache):
    v1 = Usd.Stage.Open(f"{L}{ROBOT}?v=1", context(cache))
    v2 = Usd.Stage.Open(f"{L}{ROBOT}?v=2", context(cache))
    assert v1.GetPrimAtPath("/franka/body").GetAttribute("size").Get() == 1
    assert v2.GetPrimAtPath("/franka/body").GetAttribute("size").Get() == 2
    # Both versions side by side in the cache; v2 includes the unchanged entry layer.
    assert (cache / ROBOT / "v001" / "franka.usda").is_file()
    assert (cache / ROBOT / "v002" / "franka.usda").is_file()


def test_scene_composes_with_pinned_dependency(depot, cache):
    stage = Usd.Stage.Open(f"{L}{ROBOT}?v=2", context(cache))
    # franka v2 still references servo v1, although servo v2 exists.
    servo = stage.GetPrimAtPath("/franka/joint1_servo")
    assert servo.GetAttribute("pipeline:version").Get() == 1
    assert not composition_errors(stage)
    assert not (cache / SERVO / "v002").exists()


def test_missing_version(depot, cache, capfd):
    assert resolve(cache, f"{L}{ROBOT}?v=9") == ""
    assert "no such version on the remote" in capfd.readouterr().err


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_fetched_files_are_read_only(depot, cache):
    entry = Path(resolve(cache, f"{L}{SERVO}?v=2"))
    with pytest.raises(PermissionError):
        entry.write_text("oops")
