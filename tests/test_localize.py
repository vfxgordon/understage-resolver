"""Localize: bake a scene into plain files that open without the resolver."""

import json
import os
import subprocess
import sys

import pytest
from conftest import L, ROBOT, SERVO, publish
from pxr import Ar, Sdf

from AssetLibResolver import ResolverContext
from AssetLibResolver.localize import LocalizeError, localize, main

SCENE = """#usda 1.0
(
    defaultPrim = "world"
    upAxis = "Y"
    metersPerUnit = 1
)

def Xform "world"
{{
    def "robot" (references = @{robot}@) {{}}
    def "prop" (references = @./props/box.usda@) {{}}
    asset texture = @./tex/grid.png@
}}
"""

# Opened by a separate Python with no resolver plugin: proves the output is
# plain files. Prints what composed, as JSON.
OPEN_WITHOUT_RESOLVER = """
import json, sys
from pxr import Plug, Usd
assert not Plug.Registry().GetPluginWithName("AssetLibResolver")
stage = Usd.Stage.Open(sys.argv[1])
def get(path, name):
    prim = stage.GetPrimAtPath(path)
    attr = prim.GetAttribute(name) if prim else None
    return attr.Get() if attr else None
errors = [p.GetPath().pathString for p in Usd.PrimRange.AllPrims(stage.GetPseudoRoot())
          if p.GetPrimIndex().localErrors]
print(json.dumps({
    "defaultPrim": stage.GetDefaultPrim().GetName(),
    "upAxis": stage.GetMetadata("upAxis"),
    "robot": get("/world/robot", "pipeline:version"),
    "servo": get("/world/robot/joint1_servo", "pipeline:version"),
    "body": get("/world/robot/body", "size"),
    "prop": get("/world/prop", "size"),
    "errors": errors,
}))
"""


def open_without_resolver(top):
    env = {k: v for k, v in os.environ.items() if k != "PXR_PLUGINPATH_NAME"}
    out = subprocess.run([sys.executable, "-c", OPEN_WITHOUT_RESOLVER, str(top)],
                         env=env, capture_output=True, text=True, check=True, timeout=120)
    return json.loads(out.stdout)


def all_layer_text(out):
    return "\n".join(Sdf.Layer.OpenAsAnonymous(str(p)).ExportToString()
                     for p in out.rglob("*") if p.suffix in (".usd", ".usda", ".usdc"))


@pytest.fixture
def scene(tmp_path):
    """A scene folder with an unversioned robot ref, a local layer and a texture."""
    def make(robot=f"{L}{ROBOT}"):
        d = tmp_path / "scene"
        (d / "props").mkdir(parents=True, exist_ok=True)
        (d / "tex").mkdir(exist_ok=True)
        (d / "props" / "box.usda").write_text(
            '#usda 1.0\n(defaultPrim = "box")\ndef Cube "box"\n{\n    double size = 3\n}\n')
        (d / "tex" / "grid.png").write_bytes(b"not really a png")
        (d / "workcell.usda").write_text(SCENE.format(robot=robot))
        return d / "workcell.usda"
    return make


def run(source, out, root=None, manifest=None, **kw):
    ctx = (ResolverContext(str(manifest)) if manifest
           else ResolverContext.FromPins({}, str(root)))
    with Ar.ResolverContextBinder(Ar.ResolverContext(ctx)):
        return localize(str(source), out, **kw)


def test_scene_opens_without_resolver(library, scene, write_manifest, tmp_path):
    manifest = write_manifest({ROBOT: "v001"}, root=library)
    out = tmp_path / "out"
    report = run(scene(), out, manifest=manifest)

    assert report.ok
    assert report.top_layer == out / "workcell.usda"
    assert report.assets == {ROBOT: {"v001": str(library / ROBOT / "v001")},
                             SERVO: {"v001": str(library / SERVO / "v001")}}
    for f in ("lib/robots/franka/v001/franka.usda", "lib/robots/franka/v001/geo.usda",
              "lib/components/servos/xm430/v001/xm430.usda",
              "scene/workcell.usda", "scene/props/box.usda", "scene/tex/grid.png"):
        assert (out / f).is_file(), f
    assert not (out / "lib" / ROBOT / "v002").exists()   # pinned, so not baked
    assert f"@{L}" not in all_layer_text(out)

    r = open_without_resolver(report.top_layer)
    assert r == {"defaultPrim": "world", "upAxis": "Y", "robot": 1, "servo": 1,
                 "body": 1.0, "prop": 3.0, "errors": []}

    provenance = Sdf.Layer.FindOrOpen(str(report.top_layer)).customLayerData
    assets = provenance["assetlib:localized"]["assets"]
    assert {a: list(v) for a, v in assets.items()} == {ROBOT: ["v001"], SERVO: ["v001"]}
    assert provenance["assetlib:localized"]["manifest"] == "../release.json"
    assert provenance["assetlib:localized"]["source"] == "../scene/workcell.usda"


def test_latest_is_frozen(library, scene, tmp_path):
    report = run(scene(), tmp_path / "out", root=library)
    assert report.assets.keys() == {ROBOT, SERVO}
    assert list(report.assets[ROBOT]) == ["v002"]
    assert open_without_resolver(report.top_layer)["servo"] == 2


def test_asset_uri_source(library, tmp_path):
    out = tmp_path / "out"
    report = run(f"{L}{ROBOT}?v=1", out, root=library)
    assert report.top_layer == out / "franka.usda"
    r = open_without_resolver(report.top_layer)
    assert r["defaultPrim"] == "franka" and r["errors"] == []


def test_crate_stays_crate(library, tmp_path):
    vdir = publish(library, "props/crate", 1, {})
    layer = Sdf.Layer.CreateAnonymous()
    Sdf.CreatePrimInLayer(layer, "/crate").specifier = Sdf.SpecifierDef
    layer.Export(str(vdir / "crate.usdc"))
    out = tmp_path / "out"
    run(f"{L}props/crate", out, root=library)
    assert (out / "lib/props/crate/v001/crate.usdc").read_bytes()[:8] == b"PXR-USDC"


def test_copies_are_writable(remote_library, tmp_path):
    """Fetched cache files are write-protected; their copies aren't."""
    rl = remote_library()
    out = tmp_path / "out"
    report = run(f"{L}{ROBOT}?v=2", out, root=rl.root)
    assert report.ok
    assert all(os.access(p, os.W_OK) for p in out.rglob("*"))
    assert not list(out.rglob(".assetlib.json"))     # no backend config copied


def test_unresolved_reference_is_reported(library, scene, tmp_path):
    report = run(scene(robot=f"{L}robots/missing"), tmp_path / "out", root=library)
    assert not report.ok
    assert report.failed[0][0] == f"{L}robots/missing"


def test_refuses_to_overwrite(library, scene, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "precious.txt").write_text("keep me")
    with pytest.raises(LocalizeError):
        run(scene(), out, root=library)
    with pytest.raises(LocalizeError):       # not ours: force doesn't delete it
        run(scene(), out, root=library, force=True)
    assert (out / "precious.txt").is_file()

    out2 = tmp_path / "out2"
    run(scene(), out2, root=library)
    with pytest.raises(LocalizeError):
        run(scene(), out2, root=library)
    assert run(scene(), out2, root=library, force=True).ok


def test_cli(library, scene, write_manifest, tmp_path, capsys):
    manifest = write_manifest({ROBOT: "v002"}, root=library)
    out = tmp_path / "out"
    assert main([str(scene()), "-o", str(out), "--manifest", str(manifest)]) == 0
    assert f"asset   {ROBOT} v002" in capsys.readouterr().out
    assert main([str(scene()), "-o", str(out), "--manifest", str(manifest)]) == 2
