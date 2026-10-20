"""In-Houdini checks for the AssetLibResolver package. Run with hython, with
the package enabled (HOUDINI_PACKAGE_DIR pointing at its install dir):

    $HFS/bin/hython houdini/tests/hython_checks.py <empty work dir>

Driven by tests/test_hython.py. Prints one line per check and exits non-zero
if any fail. Needs a Houdini license (unlike the rest of the suite).
"""

import json
import os
import sys
import traceback
from pathlib import Path

import hou

WORK = Path(sys.argv[1]).resolve()
SCHEME = None  # set once the package's Python module is imported
results = []


def check(name):
    def wrap(fn):
        try:
            fn()
            results.append((name, None))
        except Exception:
            results.append((name, traceback.format_exc()))
        return fn
    return wrap


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


SERVO = """#usda 1.0
(
    defaultPrim = "xm430"
)
def Xform "xm430"
{{
    custom int pipeline:version = {n}
}}
"""

ROBOT = """#usda 1.0
(
    defaultPrim = "franka"
)
def Xform "franka"
{{
    custom int pipeline:version = {n}
    def "joint1_servo" (references = @{scheme}:components/servos/xm430?v={n}@) {{}}
}}
"""

CRATE = """#usda 1.0
(
    defaultPrim = "crate"
    metersPerUnit = 1
    upAxis = "Y"
)
def Mesh "crate"
{
    int[] faceVertexCounts = [4, 4, 4, 4, 4, 4]
    int[] faceVertexIndices = [0, 1, 3, 2, 2, 3, 5, 4, 4, 5, 7, 6, 6, 7, 1, 0, 1, 7, 5, 3, 6, 0, 2, 4]
    point3f[] points = [(-0.5, -0.5, 0.5), (0.5, -0.5, 0.5), (-0.5, 0.5, 0.5), (0.5, 0.5, 0.5),
                        (-0.5, 0.5, -0.5), (0.5, 0.5, -0.5), (-0.5, -0.5, -0.5), (0.5, -0.5, -0.5)]
}
"""


def make_library(root, versions_dir=lambda n: f"v{n:03d}"):
    for n in (1, 2):
        write(root / "components/servos/xm430" / versions_dir(n) / "xm430.usda",
              SERVO.format(n=n))
        write(root / "robots/franka" / versions_dir(n) / "franka.usda",
              ROBOT.format(n=n, scheme=SCHEME))
    write(root / "props/crate" / versions_dir(1) / "crate.usda", CRATE)


def sublayer_versions(path, prim_paths):
    """Cook a Sublayer LOP on `path`; return ({prim: pipeline:version}, errors).
    The LOP's stage is only valid while the node exists, so read it here."""
    lop = hou.node("/stage").createNode("sublayer")
    try:
        lop.parm("filepath1").set(path)
        stage = lop.stage()
        return {p: version(stage, p) for p in prim_paths}, lop.errors()
    finally:
        lop.destroy()


def version(stage, path):
    prim = stage.GetPrimAtPath(path)
    return prim.GetAttribute("pipeline:version").Get() if prim else None


# --- Checks -----------------------------------------------------------------------


@check("package: Python module imports and the plugin is registered")
def _():
    global SCHEME
    import AssetLibResolver
    from pxr import Ar, Plug

    SCHEME = AssetLibResolver.GetUriScheme()
    # Creating the resolver loads every resolver plugin; another one failing
    # (e.g. an ABI-incompatible Omniverse connector) raises here once, then
    # Ar falls back. That's not ours to fail on, so report and carry on.
    from pxr import Tf
    try:
        Ar.GetResolver()
    except Tf.ErrorException as e:
        print("note: another resolver plugin failed to load: "
              + str(e).strip().splitlines()[0][:300])
    plugin = Plug.Registry().GetPluginWithName("AssetLibResolver")
    assert plugin, "AssetLibResolver plugin not registered in Houdini's USD"
    assert "/dso/usd/" in plugin.path, plugin.path
    assert Ar.GetResolver().CreateIdentifier(f"{SCHEME}://a//b") == f"{SCHEME}:a/b"


LIB = WORK / "library"
os.environ["ASSET_LIBRARY_ROOT"] = str(LIB)
if SCHEME:
    make_library(LIB)


@check("Solaris: sublayer ustage: asset with a versioned dependency")
def _():
    versions, errors = sublayer_versions(f"{SCHEME}:robots/franka?v=1",
                                         ["/franka", "/franka/joint1_servo"])
    assert not errors, errors
    assert versions == {"/franka": 1, "/franka/joint1_servo": 1}, versions


@check("Solaris: unversioned ustage: is latest")
def _():
    versions, _ = sublayer_versions(f"{SCHEME}:robots/franka",
                                    ["/franka", "/franka/joint1_servo"])
    assert versions == {"/franka": 2, "/franka/joint1_servo": 2}, versions


@check("Solaris: Python resolver context binding (release pins)")
def _():
    import AssetLibResolver
    from pxr import Ar, Usd

    ctx = AssetLibResolver.ResolverContext.FromPins({"robots/franka": "v001"}, str(LIB))
    stage = Usd.Stage.Open(f"{SCHEME}:robots/franka", Ar.ResolverContext(ctx))
    assert version(stage, "/franka") == 1


@check("Solaris: missing version is fetched from a remote during a LOP cook")
def _():
    remote, cache = WORK / "remote", WORK / "cache"
    make_library(remote, versions_dir=str)  # <remote>/<asset>/<N>/
    cache.mkdir()
    python = Path(os.environ["HFS"]) / "python" / "bin" / "python3"
    write(cache / ".assetlib.json", json.dumps({"backend": {"command": [
        str(python), "-m", "assetlib_backends.dirremote", "--remote", str(remote)]}}))
    os.environ["ASSET_LIBRARY_ROOT"] = str(cache)
    try:
        versions, errors = sublayer_versions(f"{SCHEME}:robots/franka?v=2",
                                             ["/franka/joint1_servo"])
    finally:
        os.environ["ASSET_LIBRARY_ROOT"] = str(LIB)
    assert not errors, errors
    assert versions == {"/franka/joint1_servo": 2}, versions
    entry = cache / "robots/franka/v002/franka.usda"
    assert entry.is_file()
    assert not os.access(entry, os.W_OK), "fetched file should be read-only"


@check("FS helper: hou.readFile on a ustage: URI")
def _():
    text = hou.readFile(f"{SCHEME}:robots/franka?v=1")
    assert "pipeline:version = 1" in text, text[:200]


@check("FS helper: File SOP loads a ustage: USD asset as geometry")
def _():
    geo = hou.node("/obj").createNode("geo")
    sop = geo.createNode("file")
    sop.parm("file").set(f"{SCHEME}:props/crate?v=1")
    g = sop.geometry().freeze()  # a copy that outlives the node
    errors = sop.errors()
    geo.destroy()
    assert not errors, errors
    assert g is not None and len(g.points()) > 0, "no geometry loaded"


@check("FS helper: writing to a ustage: path fails and leaves no local file")
def _():
    cwd = WORK / "cwd"
    cwd.mkdir()
    os.chdir(cwd)
    g = hou.Geometry()
    g.createPoint()
    try:
        g.saveToFile(f"{SCHEME}:props/out.bgeo")
    except hou.OperationFailed:
        pass
    else:
        raise AssertionError("saveToFile to a ustage: path should fail")
    assert list(cwd.iterdir()) == [], list(cwd.iterdir())


failed = [name for name, err in results if err]
for name, err in results:
    print(("ok      " if not err else "FAILED  ") + name)
    if err:
        print("        " + err.replace("\n", "\n        "))
print(f"{hou.applicationVersionString()}: "
      f"{len(results) - len(failed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
