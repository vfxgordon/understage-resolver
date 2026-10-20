#
# Copyright 2026 Gordon Chapman
# SPDX-License-Identifier: Apache-2.0
#
"""Bake a scene into plain files that need no asset resolver.

Some tools read USD but can't load a resolver plugin. This resolves every
asset URI in a scene once, under a release manifest or the default
context, and writes a self-contained copy with only relative file paths:

    <out>/<name>.usda                  top layer: open this one
    <out>/scene/...                    the scene's own layers, mirrored
    <out>/lib/<asset>/vNNN/...         each asset version used, mirrored
    <out>/external/...                 files from outside the scene folder

Each asset version is copied as a whole folder, so its internal files
(textures, sublayers) keep their relative paths. Every layer is rewritten:
each `ustage:` URI becomes a relative path to the copied entry layer. Crate
layers stay crate. The top layer sublayers the scene, carries its stage
metadata, and records which asset versions were baked in.

The output is a snapshot. Unversioned URIs in the scene are frozen at the
version they resolved to, so localize again to pick up a new release.

    python -m AssetLibResolver.localize scene.usda -o out/ [--manifest release.json]
"""

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pxr import Ar, Sdf, UsdUtils, Vt

from . import GetUriScheme, ResolverContext
from .prefetch import prefetch

_USD_EXTENSIONS = {".usd", ".usda", ".usdc"}
_MARKER = ".assetlib-localized.json"
# Stage metadata only counts on the root layer, so the top layer copies it
# from the scene (sublayering doesn't carry it).
_STAGE_METADATA = ("defaultPrim", "upAxis", "metersPerUnit", "kilogramsPerUnit",
                   "startTimeCode", "endTimeCode", "timeCodesPerSecond",
                   "framesPerSecond")


@dataclass
class LocalizeReport:
    top_layer: Path = None
    assets: dict = field(default_factory=dict)     # asset -> {"vNNN": source dir}
    layers_written: int = 0
    files_copied: int = 0
    failed: list = field(default_factory=list)     # (asset path, layer), left as-is

    @property
    def ok(self):
        return not self.failed


class LocalizeError(Exception):
    pass


def _is_lib_uri(path):
    return path.lower().startswith(GetUriScheme() + ":")


def _is_crate(path):
    with open(path, "rb") as f:
        return f.read(8) == b"PXR-USDC"


def _copy(src, dest):
    # copyfile, not copy2: cached library files are write-protected, and
    # the copies should be ordinary files.
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)


def _relative(target, from_dir):
    rel = os.path.relpath(target, from_dir)
    return rel if rel.startswith("..") else "./" + rel


class _Localizer:
    def __init__(self, source, out):
        self.resolver = Ar.GetResolver()
        self.out = out
        self.scheme = GetUriScheme()
        self.base = None if _is_lib_uri(source) else Path(source).resolve().parent
        self.report = LocalizeReport()
        self.versions = {}      # source version dir -> output version dir
        self.layers = {}        # source layer -> output layer
        self.pending = []       # (source layer, output layer) to rewrite

    # -- where things go --------------------------------------------------

    def _destination(self, path):
        """Output path for a resolved source file."""
        for vdir, dest in self.versions.items():
            if path.is_relative_to(vdir):
                return dest / path.relative_to(vdir)
        if self.base and path.is_relative_to(self.base):
            return self.out / "scene" / path.relative_to(self.base)
        return self.out / "external" / path.relative_to(path.anchor)

    def _add_version(self, identifier, entry):
        """Mirror the asset version holding `entry`; return the output entry."""
        vdir = entry.parent
        if vdir not in self.versions:
            asset = identifier[len(self.scheme) + 1:].split("?", 1)[0]
            dest = self.out / "lib" / asset / vdir.name
            self.versions[vdir] = dest
            self.report.assets.setdefault(asset, {})[vdir.name] = str(vdir)
            for src in sorted(p for p in vdir.rglob("*") if p.is_file()):
                out = dest / src.relative_to(vdir)
                if src.suffix in _USD_EXTENSIONS:
                    self._add_layer(src, out)
                else:
                    _copy(src, out)
                    self.report.files_copied += 1
        return self.versions[vdir] / entry.relative_to(vdir)

    def _add_layer(self, src, dest):
        if src not in self.layers:
            self.layers[src] = dest
            self.pending.append((src, dest))

    def _add_file(self, src):
        dest = self._destination(src)
        if src.suffix in _USD_EXTENSIONS:
            self._add_layer(src, dest)
        elif not dest.exists():
            _copy(src, dest)
            self.report.files_copied += 1
        return dest

    # -- rewriting --------------------------------------------------------

    def _map(self, path, anchor, layer, out_dir):
        """New text for one authored asset path in `layer`."""
        if not path or path.startswith("`"):     # empty, or an expression
            return path
        identifier = self.resolver.CreateIdentifier(path, anchor)
        if "<UDIM>" in path:
            tiles = sorted(Path(identifier).parent.glob(
                Path(identifier).name.replace("<UDIM>", "[0-9]" * 4)))
            if not tiles:
                self.report.failed.append((path, str(layer)))
                return path
            for tile in tiles:
                dest = self._add_file(tile.resolve())
            return _relative(dest.parent / Path(path).name, out_dir)
        resolved = self.resolver.Resolve(identifier)
        if not resolved:
            self.report.failed.append((path, str(layer)))
            return path
        resolved = Path(resolved.GetPathString()).resolve()
        if _is_lib_uri(identifier):
            dest = self._add_version(identifier, resolved)
        else:
            dest = self._add_file(resolved)
        return _relative(dest, out_dir)

    def _rewrite(self, src, dest):
        layer = Sdf.Layer.OpenAsAnonymous(str(src))
        anchor = Ar.ResolvedPath(str(src))
        UsdUtils.ModifyAssetPaths(
            layer, lambda path: self._map(path, anchor, src, dest.parent))
        dest.parent.mkdir(parents=True, exist_ok=True)
        # A .usd file keeps the encoding it had.
        args = {}
        if dest.suffix == ".usd":
            args["format"] = "usdc" if _is_crate(src) else "usda"
        if not layer.Export(str(dest), args=args):
            raise LocalizeError(f"could not write {dest}")
        self.report.layers_written += 1

    def run(self, source, name):
        identifier = self.resolver.CreateIdentifier(source)
        resolved = self.resolver.Resolve(identifier)
        if not resolved:
            raise LocalizeError(f"{source} does not resolve")
        resolved = Path(resolved.GetPathString()).resolve()
        root = (self._add_version(identifier, resolved) if _is_lib_uri(identifier)
                else self._add_file(resolved))
        while self.pending:
            self._rewrite(*self.pending.pop(0))

        top = self.out / f"{name}.usda"
        layer = Sdf.Layer.CreateNew(str(top))
        scene = Sdf.Layer.FindOrOpen(str(resolved))
        for key in _STAGE_METADATA:
            if scene.pseudoRoot.HasInfo(key):
                layer.pseudoRoot.SetInfo(key, scene.pseudoRoot.GetInfo(key))
        layer.subLayerPaths.append(_relative(root, self.out))
        layer.comment = (f"Localized from {source}: no asset resolver needed. "
                         "Regenerate rather than edit.")
        layer.customLayerData = {"assetlib:localized": self._provenance(source)}
        layer.Save()
        self.report.top_layer = top
        self.report.layers_written += 1
        return self.report

    def _provenance(self, source):
        context = Ar.GetResolver().GetCurrentContext().Get()
        manifest = next((c.GetManifestPath() for c in context
                         if isinstance(c, ResolverContext)), "")
        # Paths relative to the output folder, so the output carries no
        # machine-specific paths and still points home when moved alongside.
        return {
            "source": source if _is_lib_uri(source) else _relative(Path(source).resolve(), self.out),
            "manifest": _relative(Path(manifest).resolve(), self.out) if manifest else "",
            "assets": {a: Vt.StringArray(sorted(v))
                       for a, v in sorted(self.report.assets.items())},
        }


def localize(source, out, name=None, force=False, jobs=8):
    """Localize `source` (a layer path or asset URI) into directory `out`,
    under the currently bound resolver context."""
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        if not force:
            raise LocalizeError(f"{out} is not empty (use force to replace it)")
        if not (out / _MARKER).is_file():
            raise LocalizeError(f"{out} is not empty and wasn't made by localize; "
                                "refusing to delete it")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    # Fill the cache in parallel first; resolving then never blocks on a fetch.
    prefetch([source], jobs)

    name = name or Path(source.split("?", 1)[0]).stem
    report = _Localizer(source, out).run(source, name)
    (out / _MARKER).write_text(json.dumps({
        "top_layer": report.top_layer.name,
        "failed": report.failed,
    }, indent=2) + "\n")
    return report


def main(argv=None, prog="assetlib-localize"):
    parser = argparse.ArgumentParser(
        prog=prog, description=__doc__.split("\n\n")[0])
    parser.add_argument("source", help="scene layer or asset URI to localize")
    parser.add_argument("-o", "--output", required=True, help="output directory")
    parser.add_argument("--manifest", help="release manifest to resolve with")
    parser.add_argument("--name", help="top layer name (default: the source's)")
    parser.add_argument("--force", action="store_true",
                        help="replace an earlier localize output in the directory")
    parser.add_argument("-j", "--jobs", type=int, default=8,
                        help="parallel fetches (default 8)")
    args = parser.parse_args(argv)

    context = (Ar.ResolverContext(ResolverContext(args.manifest)) if args.manifest
               else Ar.GetResolver().CreateDefaultContext())
    try:
        with Ar.ResolverContextBinder(context):
            report = localize(args.source, args.output, args.name, args.force, args.jobs)
    except LocalizeError as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return 2

    for asset, versions in sorted(report.assets.items()):
        print(f"asset   {asset} {' '.join(sorted(versions))}")
    for path, layer in report.failed:
        print(f"FAILED  {path} (in {layer}): left unresolved", file=sys.stderr)
    print(f"{report.top_layer}: {report.layers_written} layers written, "
          f"{report.files_copied} files copied, {len(report.failed)} failed")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
