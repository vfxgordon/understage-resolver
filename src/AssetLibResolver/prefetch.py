#
# Copyright 2026 Gordon Chapman
# SPDX-License-Identifier: Apache-2.0
#
"""Fetch everything a scene needs from the asset library, in parallel.

Opening a stage fetches missing asset versions on demand, one at a time,
blocking composition. Prefetching first walks the scene's asset dependency
graph and fills the local cache in parallel, so the stage then opens
without touching the network.

The walk is breadth-first, one level of the dependency graph at a time.
Because of the published-asset rule, every reference below the scene is
pinned to an explicit version, so the graph is fixed by the scene and its
release manifest alone.

    python -m AssetLibResolver.prefetch scene.usda [--manifest release.json] [-j 8]
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pxr import Ar, UsdUtils

from . import GetUriScheme, Prefetch, ResolverContext

_USD_EXTENSIONS = {".usd", ".usda", ".usdc"}


@dataclass
class PrefetchReport:
    resolved: dict = field(default_factory=dict)   # uri -> local entry layer
    failed: list = field(default_factory=list)     # uris that did not resolve
    layers_scanned: int = 0

    @property
    def ok(self):
        return not self.failed


def _is_lib_uri(path):
    return path.lower().startswith(GetUriScheme() + ":")


def _dependencies(layer_path):
    """(ustage: identifiers, local files) referenced by one layer."""
    resolver = Ar.GetResolver()
    anchor = Ar.ResolvedPath(layer_path)
    sublayers, references, payloads = UsdUtils.ExtractExternalReferences(layer_path)
    uris, files = set(), set()
    for path in [*sublayers, *references, *payloads]:
        if not path:
            continue
        # CreateIdentifier canonicalizes, and applies the published-asset
        # rule (an unversioned ref from inside the library comes back as an
        # unresolvable identifier, which is then reported as a failure).
        identifier = resolver.CreateIdentifier(path, anchor)
        if _is_lib_uri(identifier):
            uris.add(identifier)
        elif identifier and Path(identifier).suffix in _USD_EXTENSIONS:
            files.add(identifier)
    return uris, files


def prefetch(roots, jobs=8):
    """Prefetch the dependency closure of `roots` (layer paths or ustage: URIs)
    under the currently bound resolver context."""
    report = PrefetchReport()
    pending_uris = {r for r in roots if _is_lib_uri(r)}
    pending_files = {str(Path(r).resolve()) for r in roots if not _is_lib_uri(r)}
    seen_files = set()

    while pending_uris or pending_files:
        # Scan local layers for dependencies.
        while pending_files:
            path = pending_files.pop()
            if path in seen_files or not Path(path).is_file():
                continue
            seen_files.add(path)
            report.layers_scanned += 1
            uris, files = _dependencies(path)
            pending_uris |= uris - report.resolved.keys() - set(report.failed)
            pending_files |= files - seen_files

        # Fetch this level of assets in parallel.
        if pending_uris:
            batch = sorted(pending_uris)
            pending_uris.clear()
            for uri, resolved in Prefetch(batch, jobs).items():
                if not resolved:
                    report.failed.append(uri)
                    continue
                report.resolved[uri] = resolved
                # Every file in the fetched version can hold further
                # (explicitly versioned) dependencies, not just the entry.
                version_dir = Path(resolved).parent
                pending_files |= {
                    str(p) for p in version_dir.rglob("*")
                    if p.suffix in _USD_EXTENSIONS and p.is_file()
                } - seen_files
    return report


def main(argv=None, prog="assetlib-prefetch"):
    parser = argparse.ArgumentParser(
        prog=prog, description=__doc__.split("\n\n")[0])
    parser.add_argument("roots", nargs="+",
                        help="scene layers and/or asset URIs to prefetch")
    parser.add_argument("--manifest", help="release manifest to resolve with")
    parser.add_argument("-j", "--jobs", type=int, default=8,
                        help="parallel fetches (default 8)")
    args = parser.parse_args(argv)

    context = (Ar.ResolverContext(ResolverContext(args.manifest)) if args.manifest
               else Ar.GetResolver().CreateDefaultContext())
    with Ar.ResolverContextBinder(context):
        report = prefetch(args.roots, args.jobs)

    for uri in sorted(report.resolved):
        print(f"ok      {uri} -> {report.resolved[uri]}")
    for uri in sorted(report.failed):
        print(f"FAILED  {uri}", file=sys.stderr)
    print(f"{len(report.resolved)} assets ready, {len(report.failed)} failed, "
          f"{report.layers_scanned} layers scanned")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
