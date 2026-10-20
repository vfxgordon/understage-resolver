"""In-Houdini checks: run houdini/tests/hython_checks.py under hython with the
built package enabled (HOUDINI_PACKAGE_DIR). Needs a Houdini license.

Skipped unless run via houdini/run_tests.sh (which sets ASSETLIB_HYTHON and
ASSETLIB_HOUDINI_PACKAGE), or if hython can't get a license.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

HYTHON = os.environ.get("ASSETLIB_HYTHON")
PACKAGE = os.environ.get("ASSETLIB_HOUDINI_PACKAGE")
CHECKS = Path(__file__).parents[1] / "houdini" / "tests" / "hython_checks.py"
pytestmark = pytest.mark.skipif(not (HYTHON and PACKAGE and os.path.isfile(HYTHON)),
                                reason="hython not configured (run houdini/run_tests.sh)")


def test_in_houdini(tmp_path):
    packages = tmp_path / "packages"
    packages.mkdir()
    shutil.copy(Path(PACKAGE) / "AssetLibResolver.json", packages)
    work = tmp_path / "work"
    work.mkdir()
    # hython sets up its own Python/USD/library paths; don't leak the
    # test runner's.
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PXR_PLUGINPATH_NAME", "LD_LIBRARY_PATH")}
    env["HOUDINI_PACKAGE_DIR"] = str(packages)
    # Only the package under test: not the user's own prefs/packages.
    env["HOUDINI_USER_PREF_DIR"] = str(tmp_path / "prefs" / "houdini__HVER__")
    proc = subprocess.run([HYTHON, str(CHECKS), str(work)], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=900)
    output = proc.stdout + proc.stderr
    if "No licenses could be found" in output:
        pytest.skip("no Houdini license available")
    print(output)
    assert proc.returncode == 0, output
