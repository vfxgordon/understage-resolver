"""Houdini FS helper: ustage: URIs through Houdini's own file layer (non-Solaris).

Runs houdini/tests/fs_helper_probe, a standalone HDK program that loads the
package's FS helpers exactly as Houdini does, so no Houdini license is
needed. Skipped unless run via houdini/run_tests.sh (which sets
ASSETLIB_HOUDINI_FS_PROBE and ASSETLIB_HOUDINI_PACKAGE).
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from conftest import ROBOT, SERVO, L, publish_servo

PROBE = os.environ.get("ASSETLIB_HOUDINI_FS_PROBE")
PACKAGE = os.environ.get("ASSETLIB_HOUDINI_PACKAGE")
pytestmark = pytest.mark.skipif(
    not (PROBE and PACKAGE and os.path.isfile(PROBE)),
    reason="Houdini FS probe not built (run houdini/run_tests.sh)")


def probe(mode, path, root, cwd, **env):
    # An empty user pref dir: the user's own packages (e.g. one setting
    # ASSET_LIBRARY_ROOT) must not leak into the test.
    prefs = Path(tempfile.mkdtemp(prefix="houdini_prefs_"))
    environ = dict(os.environ, HOUDINI_PATH=f"{PACKAGE}:&", ASSET_LIBRARY_ROOT=str(root),
                   HOUDINI_USER_PREF_DIR=str(prefs / "houdini__HVER__"), **env)
    # The helper must register the USD plugin itself: in a non-Solaris
    # session nothing else may have.
    environ.pop("PXR_PLUGINPATH_NAME", None)
    out = subprocess.run([PROBE, mode, path], env=environ, cwd=cwd, capture_output=True,
                         text=True, check=True, timeout=300)
    return json.loads(out.stdout)


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "library"
    publish_servo(root, 1)
    publish_servo(root, 2)
    return root


def test_reads_versioned_uri(lib, tmp_path):
    r = probe("read", f"{L}{SERVO}?v=1", lib, tmp_path)
    assert r["ok"] and r["exists"]
    assert "pipeline:version = 1" in r["content"]
    assert r["extension"] == ".usda"  # from the entry layer; the URI has none
    assert r["size"] == len(r["content"])


def test_unversioned_uri_reads_latest(lib, tmp_path):
    assert "pipeline:version = 2" in probe("read", f"{L}{SERVO}", lib, tmp_path)["content"]


def test_missing_asset(lib, tmp_path):
    r = probe("read", f"{L}props/missing", lib, tmp_path)
    assert not r["ok"] and not r["exists"]


def test_library_is_read_only_and_writes_leave_no_local_file(lib, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    assert probe("read", f"{L}{SERVO}?v=1", lib, work)["writable"] is False
    assert probe("write", f"{L}{SERVO}?v=1", lib, work)["ok"] is False
    assert list(work.iterdir()) == []  # no file literally named "ustage:..."


def test_fetches_from_remote(remote_library, tmp_path):
    """Same resolver, same backends: a miss in a SOP file parm fetches too."""
    rl = remote_library()
    r = probe("read", f"{L}{ROBOT}?v=2", rl.root, tmp_path)
    assert r["ok"] and "pipeline:version = 2" in r["content"]
    assert (rl.root / ROBOT / "v002" / "franka.usda").is_file()
    assert rl.calls("fetch") == [f"fetch {ROBOT} 2"]
