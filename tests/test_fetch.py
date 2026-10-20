"""Fetching published versions from a remote through a backend helper."""

import json
import os
import stat
import time
from pathlib import Path

import pytest
from conftest import L, OFFLINE_ENV, PROVENANCE_FILE, ROBOT, SERVO, composition_errors
from pxr import Ar, Sdf, Usd

import AssetLibResolver
from AssetLibResolver.prefetch import main as prefetch_main
from AssetLibResolver.prefetch import prefetch


def resolve(lib, uri):
    with Ar.ResolverContextBinder(Ar.ResolverContext(lib.context())):
        return Ar.GetResolver().Resolve(uri).GetPathString()


def version_at(stage, prim_path):
    return stage.GetPrimAtPath(prim_path).GetAttribute("pipeline:version").Get()


def leftovers(lib):
    """Temporary fetch directories left anywhere in the cache."""
    return [p for p in lib.root.rglob(".fetch-*")]


# --- Fetch on miss --------------------------------------------------------------


def test_fetch_on_miss(remote_library):
    lib = remote_library()
    path = resolve(lib, f"{L}{SERVO}?v=1")
    assert path == str(lib.root / SERVO / "v001" / "xm430.usda")
    assert Path(path).is_file()

    provenance = json.loads((lib.root / SERVO / "v001" / PROVENANCE_FILE).read_text())
    assert provenance["asset"] == SERVO
    assert provenance["version"] == "v001"
    assert provenance["source"].endswith(f"{SERVO}/1")
    assert lib.calls("fetch") == [f"fetch {SERVO} 1"]


def test_version_spellings_are_one_identifier():
    r = Ar.GetResolver()
    ids = {r.CreateIdentifier(f"{L}{SERVO}?v={v}") for v in ("1", "v1", "v001")}
    assert ids == {f"{L}{SERVO}?v=v001"}


def test_fetched_version_is_not_refetched(remote_library):
    lib = remote_library()
    resolve(lib, f"{L}{SERVO}?v=1")
    resolve(lib, f"{L}{SERVO}?v=1")
    assert len(lib.calls("fetch")) == 1


def test_composition_fetches_dependencies(remote_library):
    lib = remote_library()
    stage = Usd.Stage.Open(f"{L}{ROBOT}?v=1", Ar.ResolverContext(lib.context()))
    assert version_at(stage, "/franka") == 1
    assert version_at(stage, "/franka/joint1_servo") == 1
    assert stage.GetPrimAtPath("/franka/body").GetAttribute("size").Get() == 1
    assert not composition_errors(stage)
    # Exactly the versions needed, nothing more.
    assert sorted(lib.calls("fetch")) == [f"fetch {SERVO} 1", f"fetch {ROBOT} 1"]


# --- Latest ---------------------------------------------------------------------


def test_latest_includes_remote_versions(remote_library):
    lib = remote_library()
    resolve(lib, f"{L}{SERVO}?v=1")  # only v001 is local now
    assert "/v002/" in resolve(lib, f"{L}{SERVO}")


def test_latest_listing_is_cached(remote_library):
    lib = remote_library(latest_ttl_s=3600)
    resolve(lib, f"{L}{SERVO}")
    resolve(lib, f"{L}{SERVO}")
    assert len(lib.calls("list-versions")) == 1


def test_latest_listing_expires(remote_library):
    lib = remote_library(latest_ttl_s=0)
    resolve(lib, f"{L}{SERVO}")
    resolve(lib, f"{L}{SERVO}")
    assert len(lib.calls("list-versions")) == 2


def test_new_asset_version_skips_unfetched_remote_versions(remote_library):
    lib = remote_library()
    with Ar.ResolverContextBinder(Ar.ResolverContext(lib.context())):
        path = Ar.GetResolver().ResolveForNewAsset(f"{L}{SERVO}").GetPathString()
    assert path == str(lib.root / SERVO / "v003" / "xm430.usda")


# --- Offline --------------------------------------------------------------------


def test_offline_uses_only_the_cache(remote_library, monkeypatch):
    lib = remote_library()
    resolve(lib, f"{L}{SERVO}?v=1")
    calls = len(lib.calls())

    monkeypatch.setenv(OFFLINE_ENV, "1")
    assert AssetLibResolver.IsOffline()
    assert resolve(lib, f"{L}{SERVO}?v=1").endswith("/v001/xm430.usda")
    assert resolve(lib, f"{L}{SERVO}?v=2") == ""
    assert "/v001/" in resolve(lib, f"{L}{SERVO}")  # latest = latest *cached*
    assert len(lib.calls()) == calls


# --- Concurrency and failures ---------------------------------------------------


def test_concurrent_requests_share_one_fetch(remote_library, monkeypatch):
    lib = remote_library()
    monkeypatch.setenv("ASSETLIB_TEST_DELAY", "1")
    uri = f"{L}{SERVO}?v=1"
    with Ar.ResolverContextBinder(Ar.ResolverContext(lib.context())):
        start = time.monotonic()
        result = AssetLibResolver.Prefetch([uri] * 8, jobs=8)
        elapsed = time.monotonic() - start
    assert result[uri].endswith("/v001/xm430.usda")
    assert lib.calls("fetch") == [f"fetch {SERVO} 1"]
    assert elapsed < 4


def test_failed_fetch_leaves_nothing_and_backs_off(remote_library, monkeypatch, capfd):
    lib = remote_library(failure_ttl_s=3600)
    monkeypatch.setenv("ASSETLIB_TEST_FAIL", f"{SERVO} 1")

    assert resolve(lib, f"{L}{SERVO}?v=1") == ""
    assert not (lib.root / SERVO / "v001").exists()
    assert not leftovers(lib)
    assert "simulated failure" in capfd.readouterr().err

    # Remembered: not retried immediately.
    assert resolve(lib, f"{L}{SERVO}?v=1") == ""
    assert len(lib.calls("fetch")) == 1


def test_failed_fetch_is_retried_after_backoff(remote_library, monkeypatch):
    lib = remote_library(failure_ttl_s=0)
    monkeypatch.setenv("ASSETLIB_TEST_FAIL", f"{SERVO} 1")
    assert resolve(lib, f"{L}{SERVO}?v=1") == ""
    monkeypatch.delenv("ASSETLIB_TEST_FAIL")
    assert resolve(lib, f"{L}{SERVO}?v=1").endswith("/v001/xm430.usda")
    assert len(lib.calls("fetch")) == 2


def test_missing_remote_version(remote_library, capfd):
    lib = remote_library()
    assert resolve(lib, f"{L}{SERVO}?v=9") == ""
    assert "no such version on the remote" in capfd.readouterr().err


def test_timeout_kills_the_helper(remote_library, monkeypatch):
    lib = remote_library(timeout_s=0.5)
    monkeypatch.setenv("ASSETLIB_TEST_DELAY", "30")
    start = time.monotonic()
    assert resolve(lib, f"{L}{SERVO}?v=1") == ""
    assert time.monotonic() - start < 5
    assert not leftovers(lib)


# --- Write protection -----------------------------------------------------------


def _writable_paths(top):
    return [p for p in [top, *top.rglob("*")]
            if p.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_fetched_versions_are_write_protected(remote_library):
    lib = remote_library()
    entry = Path(resolve(lib, f"{L}{ROBOT}?v=1"))
    vdir = entry.parent
    assert not _writable_paths(vdir)

    with pytest.raises(PermissionError):
        entry.write_text("oops")
    with pytest.raises(PermissionError):
        (vdir / "new.usda").write_text("oops")
    with pytest.raises(PermissionError):
        (vdir / "geo.usda").unlink()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_saving_a_fetched_layer_fails(remote_library):
    lib = remote_library()
    stage = Usd.Stage.Open(f"{L}{ROBOT}?v=1", Ar.ResolverContext(lib.context()))
    layer = stage.GetRootLayer()
    original = Path(layer.realPath).read_text()
    layer.comment = "accidental edit"
    with pytest.raises(Exception):
        layer.Save()
    assert Path(layer.realPath).read_text() == original


def test_cannot_create_layers_in_a_fetched_version(remote_library, capfd):
    lib = remote_library()
    resolve(lib, f"{L}{SERVO}?v=1")
    with Ar.ResolverContextBinder(Ar.ResolverContext(lib.context())):
        r = Ar.GetResolver()
        assert r.ResolveForNewAsset(f"{L}{SERVO}?v=1").GetPathString() == ""
        assert "read-only copy of a published revision" in capfd.readouterr().err
        # The next version is still writable.
        assert r.ResolveForNewAsset(f"{L}{SERVO}").GetPathString().endswith(
            "/v003/xm430.usda")


# --- Prefetch -------------------------------------------------------------------


def _scene(tmp_path, *refs):
    path = tmp_path / "scene.usda"
    body = "".join(f'def "item{i}" (references = @{ref}@) {{}}\n' for i, ref in enumerate(refs))
    path.write_text("#usda 1.0\n" + body)
    return path


def test_prefetch_then_open_needs_no_fetches(remote_library, tmp_path):
    lib = remote_library()
    scene = _scene(tmp_path, f"{L}{ROBOT}?v=1", f"{L}{ROBOT}?v=2")
    with Ar.ResolverContextBinder(Ar.ResolverContext(lib.context())):
        report = prefetch([str(scene)], jobs=4)
    assert report.ok
    assert sorted(report.resolved) == sorted(
        f"{L}{a}?v=v00{n}" for a in (ROBOT, SERVO) for n in (1, 2))

    calls = len(lib.calls())
    stage = Usd.Stage.Open(str(scene), Ar.ResolverContext(lib.context()))
    assert version_at(stage, "/item0/joint1_servo") == 1
    assert version_at(stage, "/item1/joint1_servo") == 2
    assert not composition_errors(stage)
    assert len(lib.calls()) == calls


def test_prefetch_cli_reports_failures(remote_library, tmp_path, capsys, monkeypatch):
    lib = remote_library()
    monkeypatch.setenv(AssetLibResolver.GetRootEnvVar(), str(lib.root))
    scene = _scene(tmp_path, f"{L}{ROBOT}?v=1", f"{L}props/missing?v=1")
    assert prefetch_main([str(scene), "-j", "2"]) == 1
    out = capsys.readouterr()
    assert f"FAILED  {L}props/missing?v=v001" in out.err
    assert "2 assets ready, 1 failed" in out.out


def test_layer_written_via_sdf_still_works_in_local_only_library(tmp_path):
    """Sanity: no config file -> local-only, writes behave as before."""
    root = tmp_path / "lib"
    ctx = AssetLibResolver.ResolverContext.FromPins({}, str(root))
    with Ar.ResolverContextBinder(Ar.ResolverContext(ctx)):
        Sdf.Layer.CreateNew(f"{L}props/thing").Save()
    assert (root / "props/thing/v001/thing.usda").is_file()
    assert not (root / "props/thing/v001" / PROVENANCE_FILE).exists()
