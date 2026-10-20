"""Test setup: point Plug and Python at the installed plugin, build a library.

The default is the standalone install (./install). To test another build,
set ASSETLIB_INSTALL to its install prefix, or set the three locations
individually (e.g. for the Houdini package, see houdini/run_tests.sh):

    ASSETLIB_PLUGIN_PATH    directory with the plugin's plugInfo.json
    ASSETLIB_PYTHON_PATH    directory containing the AssetLibResolver package
    ASSETLIB_BACKENDS_PATH  directory containing assetlib_backends
"""

import json
import os
import sys
from pathlib import Path

import pytest

INSTALL = Path(os.environ.get("ASSETLIB_INSTALL", Path(__file__).parents[1] / "install"))
PLUGIN_PATH = os.environ.get("ASSETLIB_PLUGIN_PATH", str(INSTALL / "plugin" / "usd"))
PYTHON_PATH = os.environ.get("ASSETLIB_PYTHON_PATH", str(INSTALL / "lib" / "python"))
BACKENDS_PATH = os.environ.get("ASSETLIB_BACKENDS_PATH", PYTHON_PATH)

# Must happen before pxr is first imported: Plug reads this once.
os.environ["PXR_PLUGINPATH_NAME"] = os.pathsep.join(
    p for p in (PLUGIN_PATH, os.environ.get("PXR_PLUGINPATH_NAME")) if p
)
sys.path.insert(0, PYTHON_PATH)
# Backend helpers run as subprocesses (python -m assetlib_backends.*).
os.environ["PYTHONPATH"] = os.pathsep.join(
    p for p in (BACKENDS_PATH, os.environ.get("PYTHONPATH")) if p
)

try:
    import AssetLibResolver  # noqa: E402
except ImportError:  # Reported properly by pytest_sessionstart.
    AssetLibResolver = None

# Build-time configuration (CMake ASSETLIB_* variables), read from the build
# under test so the suite works for any configured scheme / variable names.
SCHEME = AssetLibResolver.GetUriScheme() if AssetLibResolver else "ustage"
L = SCHEME + ":"
ROOT_ENV = AssetLibResolver.GetRootEnvVar() if AssetLibResolver else ""
MANIFEST_ENV = AssetLibResolver.GetManifestEnvVar() if AssetLibResolver else ""
OFFLINE_ENV = AssetLibResolver.GetOfflineEnvVar() if AssetLibResolver else ""
CONFIG_FILE = AssetLibResolver.GetConfigFileName() if AssetLibResolver else ""
PROVENANCE_FILE = AssetLibResolver.GetProvenanceFileName() if AssetLibResolver else ""

SERVO = "components/servos/xm430"
ROBOT = "robots/franka"

SERVO_LAYER = """#usda 1.0
(
    defaultPrim = "xm430"
)

def Xform "xm430"
{{
    custom int pipeline:version = {num}
}}
"""

ROBOT_LAYER = """#usda 1.0
(
    defaultPrim = "franka"
    subLayers = [@./geo.usda@]
)

def Xform "franka" (
    kind = "component"
)
{{
    custom int pipeline:version = {num}

    def "joint1_servo" (
        references = @{servo_ref}@
    )
    {{
    }}
}}
"""

GEO_LAYER = """#usda 1.0

over "franka"
{{
    def Cube "body"
    {{
        double size = {num}
    }}
}}
"""


def publish(root: Path, asset: str, num: int, files: dict[str, str],
            dirname: str | None = None) -> Path:
    """Write files into <root>/<asset>/vNNN/ (or <dirname>) and return it."""
    vdir = root / asset / (dirname or f"v{num:03d}")
    vdir.mkdir(parents=True)
    for name, text in files.items():
        (vdir / name).write_text(text)
    return vdir


def servo_files(num: int) -> dict[str, str]:
    return {"xm430.usda": SERVO_LAYER.format(num=num)}


def robot_files(num: int, servo_ref: str, leaf: str = "franka") -> dict[str, str]:
    return {
        f"{leaf}.usda": ROBOT_LAYER.format(num=num, servo_ref=servo_ref).replace("franka", leaf),
        "geo.usda": GEO_LAYER.format(num=num).replace("franka", leaf),
    }


def publish_servo(root: Path, num: int) -> Path:
    return publish(root, SERVO, num, servo_files(num))


def publish_robot(root: Path, num: int, servo_ref: str, asset: str = ROBOT) -> Path:
    return publish(root, asset, num, robot_files(num, servo_ref, asset.rsplit("/", 1)[-1]))


class RemoteLibrary:
    """An empty local library whose backend is a directory "remote".

    remote: franka 1 -> servo ?v=1, franka 2 -> servo ?v=2; servo 1, 2.
    """

    def __init__(self, tmp_path: Path, **backend_options):
        self.root = tmp_path / "cache"
        self.remote = tmp_path / "remote"
        self.log = tmp_path / "backend.log"
        self.root.mkdir()
        for num in (1, 2):
            publish(self.remote, SERVO, num, servo_files(num), dirname=str(num))
            publish(self.remote, ROBOT, num,
                    robot_files(num, f"{L}{SERVO}?v={num}"), dirname=str(num))
        backend = {
            "command": [sys.executable, "-m", "assetlib_backends.dirremote",
                        "--remote", str(self.remote)],
            **backend_options,
        }
        (self.root / CONFIG_FILE).write_text(json.dumps({"backend": backend}))

    def context(self):
        return AssetLibResolver.ResolverContext.FromPins({}, str(self.root))

    def calls(self, command=None):
        """Backend invocations so far, optionally only of one command."""
        if not self.log.exists():
            return []
        lines = self.log.read_text().splitlines()
        return [ln for ln in lines if command is None or ln.split()[0] == command]


@pytest.fixture
def remote_library(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSETLIB_TEST_LOG", str(tmp_path / "backend.log"))
    monkeypatch.delenv(OFFLINE_ENV, raising=False)

    def make(**backend_options):
        return RemoteLibrary(tmp_path, **backend_options)

    return make


@pytest.fixture
def library(tmp_path):
    """A small library:

    components/servos/xm430/v001, v002      (pipeline:version = 1, 2)
    robots/franka/v001 -> xm430?v=v001     (robot version 1)
    robots/franka/v002 -> xm430?v=v002     (robot version 2, latest)
    """
    root = tmp_path / "library"
    publish_servo(root, 1)
    publish_servo(root, 2)
    publish_robot(root, 1, f"{L}{SERVO}?v=v001")
    publish_robot(root, 2, f"{L}{SERVO}?v=v002")
    return root


@pytest.fixture
def write_manifest(tmp_path):
    def _write(pins, root=None, name="release.json"):
        path = tmp_path / name
        doc = {"assets": pins}
        if root is not None:
            doc["root"] = str(root)
        path.write_text(json.dumps(doc))
        return path

    return _write


def composition_errors(stage):
    """All composition errors on a stage. Usd.Stage.GetCompositionErrors()
    is newer than some USD builds we test (Houdini 20.5 ships USD 24.03), so
    fall back to collecting every prim index's local errors."""
    if hasattr(stage, "GetCompositionErrors"):
        return list(stage.GetCompositionErrors())
    from pxr import Usd

    errors = []
    for prim in Usd.PrimRange.AllPrims(stage.GetPseudoRoot()):
        errors.extend(prim.GetPrimIndex().localErrors)
    return errors


def pytest_sessionstart(session):
    """Fail fast if the plugin isn't loaded; otherwise the default resolver
    treats our URIs as relative file paths and tests write junk into the cwd."""
    from pxr import Plug

    if not Plug.Registry().GetPluginWithName("AssetLibResolver"):
        pytest.exit(f"AssetLibResolver plugin not found under {PLUGIN_PATH}; build and "
                    "install it first (see README)", returncode=2)
