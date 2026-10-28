import json
import os
import subprocess
import sys

import pytest
from conftest import (
    composition_errors,
    DRAFT_FILE,
    MANIFEST_ENV,
    ROBOT,
    ROOT_ENV,
    SCHEME,
    SERVO,
    L,
    publish,
    publish_robot,
    publish_servo,
    servo_files,
)
from pxr import Ar, Pcp, Sdf, Usd

import AssetLibResolver

ResolverContext = AssetLibResolver.ResolverContext


@pytest.fixture
def resolver():
    return Ar.GetResolver()


def bound(ctx):
    return Ar.ResolverContextBinder(Ar.ResolverContext(ctx))


def resolve(path, ctx):
    with bound(ctx):
        return Ar.GetResolver().Resolve(path).GetPathString()


def version_at(stage, prim_path):
    return stage.GetPrimAtPath(prim_path).GetAttribute("pipeline:version").Get()


def open_stage(path_or_layer, ctx):
    return Usd.Stage.Open(path_or_layer, Ar.ResolverContext(ctx))


def scene_layer(*refs):
    """Anonymous scene (outside the library) with one prim per reference."""
    body = "".join(
        f'def "item{i}" (references = @{ref}@) {{}}\n' for i, ref in enumerate(refs)
    )
    layer = Sdf.Layer.CreateAnonymous(".usda")
    layer.ImportFromString("#usda 1.0\n" + body)
    return layer


# --- Identifiers --------------------------------------------------------------


@pytest.mark.parametrize(
    "given, expected",
    [
        (f"{L}{SERVO}", f"{L}{SERVO}"),
        (f"{L}//components//servos/./xm430", f"{L}{SERVO}"),
        (f"{L.upper()}robots/franka?v=v001", f"{L}robots/franka?v=v001"),
    ],
)
def test_create_identifier_canonicalizes(resolver, given, expected):
    assert resolver.CreateIdentifier(given) == expected


def test_extension_comes_from_entry_layer(library):
    publish(library, "props/crate", 1, {"crate.usdc": ""})
    ctx = ResolverContext.FromPins({}, str(library))
    with bound(ctx):
        r = Ar.GetResolver()
        assert r.GetExtension(f"{L}{SERVO}?v=v001") == "usda"
        assert r.GetExtension(f"{L}props/crate") == "usdc"
        # Not created yet: new assets are written as .usda.
        assert r.GetExtension(f"{L}props/newthing") == "usda"


# --- Resolution ---------------------------------------------------------------


def test_resolves_entry_layer_of_latest(library):
    ctx = ResolverContext.FromPins({}, str(library))
    assert resolve(f"{L}{SERVO}", ctx) == str(library / SERVO / "v002" / "xm430.usda")
    assert resolve(f"{L}{ROBOT}", ctx) == str(library / ROBOT / "v002" / "franka.usda")


def test_numeric_not_lexical_latest(library):
    publish_servo(library, 10)
    ctx = ResolverContext.FromPins({}, str(library))
    assert "/v010/" in resolve(f"{L}{SERVO}", ctx)


def test_explicit_version(library):
    ctx = ResolverContext.FromPins({}, str(library))
    assert "/v001/" in resolve(f"{L}{SERVO}?v=v001", ctx)


def test_pin_by_category_path_then_explicit_latest_overrides(library):
    ctx = ResolverContext.FromPins({SERVO: "v001"}, str(library))
    assert "/v001/" in resolve(f"{L}{SERVO}", ctx)
    assert "/v002/" in resolve(f"{L}{SERVO}?v=latest", ctx)
    # Pins are per asset.
    assert "/v002/" in resolve(f"{L}{ROBOT}", ctx)


def test_entry_layer_extension_probing(library):
    publish(library, "props/crate", 1, {"crate.usdc": ""})
    ctx = ResolverContext.FromPins({}, str(library))
    assert resolve(f"{L}props/crate", ctx).endswith("/props/crate/v001/crate.usdc")


def test_ambiguous_entry_layer_is_refused(library):
    publish(library, "props/crate", 1, {"crate.usda": "#usda 1.0\n", "crate.usdc": ""})
    ctx = ResolverContext.FromPins({}, str(library))
    assert resolve(f"{L}props/crate", ctx) == ""


@pytest.mark.parametrize(
    "path",
    [
        f"{L}nosuch/asset",
        f"{L}{SERVO}?v=v999",
        f"{L}components/servos",  # a category, not an asset
        f"{L}robots/v001",  # version-shaped segment
        f"{L}robots/../components/servos/xm430",  # escapes
        f"{L}",  # no asset
        f"{L}{SERVO}?rev=1",  # unsupported query
        f"{L}{SERVO}?v=abc",  # malformed version
        f"{L}{SERVO}?v=v0",  # versions start at 1
    ],
)
def test_unresolvable_paths_are_empty(library, path):
    assert resolve(path, ResolverContext.FromPins({}, str(library))) == ""


def test_env_root_used_without_context(library, monkeypatch, resolver):
    monkeypatch.setenv(ROOT_ENV, str(library))
    assert "/v002/" in resolver.Resolve(f"{L}{SERVO}").GetPathString()


def test_asset_info_reports_selected_version(library):
    ctx = ResolverContext.FromPins({SERVO: "v001"}, str(library))
    with bound(ctx):
        r = Ar.GetResolver()
        info = r.GetAssetInfo(f"{L}{SERVO}", r.Resolve(f"{L}{SERVO}"))
    assert (info.assetName, info.version) == (SERVO, "v001")


# --- Contexts -----------------------------------------------------------------


def test_context_identity(library, write_manifest):
    m = write_manifest({ROBOT: "v001"}, library)
    assert ResolverContext(str(m)) == ResolverContext(str(m))
    assert hash(ResolverContext(str(m))) == hash(ResolverContext(str(m)))
    a = ResolverContext.FromPins({ROBOT: "v001"}, str(library))
    b = ResolverContext.FromPins({ROBOT: "v002"}, str(library))
    assert a != b
    assert a == ResolverContext.FromPins({ROBOT: "v001"}, str(library))


def test_manifest_root_relative_to_manifest(library, write_manifest):
    m = write_manifest({ROBOT: "v001"}, "library")  # relative root
    ctx = ResolverContext(str(m))
    assert ctx.GetLibraryRoot() == str(library)
    assert ctx.GetPins() == {ROBOT: "v001"}


def test_create_context_from_string(library, write_manifest):
    m = write_manifest({ROBOT: "v001"}, library)
    ctx = Ar.GetResolver().CreateContextFromString(SCHEME, str(m))
    with Ar.ResolverContextBinder(ctx):
        assert "/v001/" in Ar.GetResolver().Resolve(f"{L}{ROBOT}").GetPathString()


def test_default_context_from_environment(library, write_manifest):
    """The manifest env var feeds the default context (fresh process)."""
    m = write_manifest({ROBOT: "v001"}, library)
    code = (
        "from pxr import Usd;"
        f"s = Usd.Stage.Open('{L}{ROBOT}');"
        "print(s.GetPrimAtPath('/franka').GetAttribute('pipeline:version').Get())"
    )
    env = dict(os.environ, **{MANIFEST_ENV: str(m)})
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                         text=True, check=True)
    assert out.stdout.strip() == "1"


def test_refresh_without_change_is_noop(library, write_manifest):
    ctx = ResolverContext(str(write_manifest({ROBOT: "v001"}, library)))
    assert ctx.Reload() is False


# --- Composition --------------------------------------------------------------


def test_relative_sublayer_stays_in_same_version(library):
    """geo.usda is authored as ./geo.usda; it must come from the pinned version."""
    stage = open_stage(f"{L}{ROBOT}", ResolverContext.FromPins({ROBOT: "v001"}, str(library)))
    assert version_at(stage, "/franka") == 1
    assert stage.GetPrimAtPath("/franka/body").GetAttribute("size").Get() == 1


def test_dependency_version_is_locked_by_the_referencing_asset(library):
    """franka v001 says xm430?v=v001; a scene-level servo pin cannot override it."""
    ctx = ResolverContext.FromPins({ROBOT: "v001", SERVO: "v002"}, str(library))
    stage = open_stage(scene_layer(f"{L}{ROBOT}"), ctx)
    assert version_at(stage, "/item0") == 1
    assert version_at(stage, "/item0/joint1_servo") == 1
    assert not composition_errors(stage)


def test_bumping_asset_pin_brings_its_dependency_along(library, write_manifest):
    manifest = write_manifest({ROBOT: "v001"}, library)
    stage = open_stage(scene_layer(f"{L}{ROBOT}"), ResolverContext(str(manifest)))
    assert version_at(stage, "/item0/joint1_servo") == 1

    write_manifest({ROBOT: "v002"}, library)
    Ar.GetResolver().RefreshContext(Ar.ResolverContext(ResolverContext(str(manifest))))
    assert version_at(stage, "/item0") == 2
    assert version_at(stage, "/item0/joint1_servo") == 2
    assert stage.GetPrimAtPath("/item0/body").GetAttribute("size").Get() == 2


# --- Published-asset rule -----------------------------------------------------


@pytest.mark.parametrize("ref", [f"{L}{SERVO}", f"{L}{SERVO}?v=latest"])
def test_unversioned_ref_inside_published_asset_is_refused(library, ref, capfd):
    publish_robot(library, 1, ref, asset="robots/broken")
    stage = open_stage(f"{L}robots/broken", ResolverContext.FromPins({}, str(library)))

    # The servo did not compose, and did not silently float to latest...
    assert version_at(stage, "/broken/joint1_servo") is None
    # ...and the failure is a visible composition error naming the reason,
    # not an internal reference (an empty asset path would mean that).
    errors = composition_errors(stage)
    assert len(errors) == 1
    assert isinstance(errors[0], Pcp.ErrorInvalidAssetPath)
    assert "unversioned-ref-in-published-asset" in str(errors[0])
    # The warning names the offending published layer.
    assert "robots/broken/v001/broken.usda" in capfd.readouterr().err


def test_unversioned_ref_outside_library_floats(library):
    """Scenes and workfiles may use unversioned refs: pin, else latest."""
    pinned = open_stage(scene_layer(f"{L}{SERVO}"),
                        ResolverContext.FromPins({SERVO: "v001"}, str(library)))
    assert version_at(pinned, "/item0") == 1
    latest = open_stage(scene_layer(f"{L}{SERVO}"), ResolverContext.FromPins({}, str(library)))
    assert version_at(latest, "/item0") == 2


def test_versioned_ref_from_file_layer_outside_library(library, tmp_path):
    """The rule keys off the library root: a work file elsewhere is not 'published'."""
    work = tmp_path / "work" / "v001" / "shot.usda"  # vNNN, but not under the root
    work.parent.mkdir(parents=True)
    work.write_text(f'#usda 1.0\ndef "s" (references = @{L}{SERVO}@) {{}}\n')
    stage = open_stage(str(work), ResolverContext.FromPins({}, str(library)))
    assert version_at(stage, "/s") == 2


# --- Writing ------------------------------------------------------------------


def test_unversioned_create_new_writes_next_version(library):
    ctx = ResolverContext.FromPins({}, str(library))
    with bound(ctx):
        layer = Sdf.Layer.CreateNew(f"{L}components/servos/new")
        assert layer is not None
        layer.Save()
        del layer
        assert (library / "components/servos/new/v001/new.usda").is_file()

        # Existing servo has v001, v002: a new write must not overwrite v002.
        resolved = Ar.GetResolver().ResolveForNewAsset(f"{L}{SERVO}")
        assert resolved.GetPathString() == str(library / SERVO / "v003" / "xm430.usda")


def test_create_new_into_explicit_version(library):
    ctx = ResolverContext.FromPins({}, str(library))
    with bound(ctx):
        Sdf.Layer.CreateNew(f"{L}components/servos/new?v=v005").Save()
    assert (library / "components/servos/new/v005/new.usda").is_file()


# --- Drafts ---------------------------------------------------------------------


def test_latest_skips_drafts(library):
    """A draft is invisible to unpinned and ?v=latest references; an explicit
    version or a pin still reaches it."""
    publish(library, SERVO, 3, servo_files(3), draft=True)
    ctx = ResolverContext.FromPins({}, str(library))
    assert resolve(f"{L}{SERVO}", ctx).endswith("/v002/xm430.usda")
    assert resolve(f"{L}{SERVO}?v=latest", ctx).endswith("/v002/xm430.usda")
    assert resolve(f"{L}{SERVO}?v=v003", ctx).endswith("/v003/xm430.usda")
    pinned = ResolverContext.FromPins({SERVO: "v003"}, str(library))
    assert resolve(f"{L}{SERVO}", pinned).endswith("/v003/xm430.usda")


def test_asset_with_only_drafts_has_no_latest(library):
    publish(library, "props/wip", 1, {"wip.usda": "#usda 1.0\n"}, draft=True)
    ctx = ResolverContext.FromPins({}, str(library))
    assert resolve(f"{L}props/wip", ctx) == ""
    assert resolve(f"{L}props/wip?v=v001", ctx).endswith("/v001/wip.usda")


def test_new_asset_goes_into_a_new_draft_then_stays_there(library):
    ctx = ResolverContext.FromPins({}, str(library))
    with bound(ctx):
        r = Ar.GetResolver()
        first = r.ResolveForNewAsset(f"{L}{SERVO}").GetPathString()
        # The draft exists, marked, before anything is written into it.
        marker = library / SERVO / "v003" / DRAFT_FILE
        assert first == str(library / SERVO / "v003" / "xm430.usda")
        assert json.loads(marker.read_text())["version"] == "v003"
        # Writing again targets the same open draft, not v004.
        assert r.ResolveForNewAsset(f"{L}{SERVO}").GetPathString() == first
        Sdf.Layer.CreateNew(f"{L}{SERVO}").Save()
    assert (library / SERVO / "v003" / "xm430.usda").is_file()
    # Still a draft, so readers keep getting v002.
    assert resolve(f"{L}{SERVO}", ctx).endswith("/v002/xm430.usda")


def test_new_draft_follows_a_published_newest_version(library):
    publish(library, SERVO, 3, servo_files(3), draft=True)
    publish(library, SERVO, 4, servo_files(4))  # published after the v003 draft
    ctx = ResolverContext.FromPins({}, str(library))
    with bound(ctx):
        path = Ar.GetResolver().ResolveForNewAsset(f"{L}{SERVO}").GetPathString()
    assert path.endswith("/v005/xm430.usda")
    # An explicit version still reaches the older draft.
    with bound(ctx):
        path = Ar.GetResolver().ResolveForNewAsset(f"{L}{SERVO}?v=v003").GetPathString()
    assert path.endswith("/v003/xm430.usda")


def test_published_versions_are_not_written(library, capfd):
    ctx = ResolverContext.FromPins({}, str(library))
    with bound(ctx):
        assert Ar.GetResolver().ResolveForNewAsset(f"{L}{SERVO}?v=v002").GetPathString() == ""
    assert "it is published, not a draft" in capfd.readouterr().err
    assert not (library / SERVO / "v002" / DRAFT_FILE).exists()
