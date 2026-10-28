#
# Copyright 2026 Gordon Chapman
# SPDX-License-Identifier: Apache-2.0
#
"""Shared command-line plumbing for backend helpers (protocol v1).

A backend implements two operations:

    list_versions(asset) -> list[int]        ascending; [] if unknown asset
    fetch(asset, version, dest: Path) -> str  writes the asset's files into
                                              the empty dest dir; returns a
                                              free-form provenance string

and raises NotFound if the asset/version does not exist remotely. Anything
else that goes wrong should raise (or exit) with a message on stderr.

A backend that can also check new versions in implements

    publish(asset, version, src: Path, message) -> str
                                              submits the files in src as the
                                              asset's new content, which must
                                              become remote version `version`;
                                              returns provenance

and raises Conflict, before submitting anything, if the remote's next version
isn't `version`. Without the method, publish fails with a clear message.
"""

import argparse
import json
import re
import sys
from pathlib import Path

PROTOCOL_VERSION = 1

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_FOUND = 2
EXIT_CONFLICT = 3

_ASSET_RE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")


class NotFound(Exception):
    """The asset or version does not exist on the remote."""


class Conflict(Exception):
    """The remote's next version is not the one being published."""


class BackendError(Exception):
    """The backend failed; the message is reported to the user."""


def check_asset(asset: str) -> str:
    """Reject asset paths that could escape the repository root."""
    if not _ASSET_RE.match(asset) or any(seg in (".", "..") for seg in asset.split("/")):
        raise BackendError(f"invalid asset path {asset!r}")
    return asset


def safe_join(dest: Path, rel: str) -> Path:
    """dest/rel, refusing anything that would land outside dest."""
    target = (dest / rel).resolve()
    if dest.resolve() not in target.parents:
        raise BackendError(f"refusing to write outside the destination: {rel!r}")
    return target


def asset_files(src: Path) -> list[str]:
    """Relative paths of every file under src, sorted. Symlinks are refused:
    a published version must be self-contained."""
    files = []
    for path in sorted(src.rglob("*")):
        if path.is_symlink():
            raise BackendError(f"refusing to publish a symlink: {path}")
        if path.is_file():
            files.append(path.relative_to(src).as_posix())
    return files


def check_next_version(asset: str, version: int, existing: int) -> None:
    """Raise Conflict unless `version` follows the remote's `existing` count."""
    if version != existing + 1:
        raise Conflict(f"{asset}: the remote has {existing} version(s), so the next one is "
                       f"{existing + 1}, not {version}")


def main(backend_factory, add_arguments=None, argv=None) -> int:
    """Parse argv, construct the backend, run one protocol command."""
    parser = argparse.ArgumentParser()
    if add_arguments:
        add_arguments(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("list-versions")
    p.add_argument("asset")
    p = sub.add_parser("fetch")
    p.add_argument("asset")
    p.add_argument("version", type=int)
    p.add_argument("dest", type=Path)
    p = sub.add_parser("publish")
    p.add_argument("asset")
    p.add_argument("version", type=int)
    p.add_argument("src", type=Path)
    p.add_argument("-m", "--message", default="")
    args = parser.parse_args(argv)

    try:
        backend = backend_factory(args)
        asset = check_asset(args.asset)
        if args.command == "list-versions":
            out = {"versions": sorted(backend.list_versions(asset))}
        elif args.command == "publish":
            if not hasattr(backend, "publish"):
                raise BackendError(f"{type(backend).__name__} backend cannot publish")
            if args.version < 1:
                raise BackendError(f"version {args.version} (versions start at 1)")
            if not args.src.is_dir() or not any(p.is_file() for p in args.src.rglob("*")):
                raise BackendError(f"nothing to publish in {args.src}")
            message = args.message or f"Publish {asset} v{args.version:03d}"
            out = {"source": backend.publish(asset, args.version, args.src, message)}
        else:
            if args.version < 1:
                raise NotFound(f"version {args.version} (versions start at 1)")
            if not args.dest.is_dir():
                raise BackendError(f"destination {args.dest} is not a directory")
            out = {"source": backend.fetch(asset, args.version, args.dest)}
    except NotFound as e:
        print(f"not found: {e}", file=sys.stderr)
        return EXIT_NOT_FOUND
    except Conflict as e:
        print(f"conflict: {e}", file=sys.stderr)
        return EXIT_CONFLICT
    except BackendError as e:
        print(str(e), file=sys.stderr)
        return EXIT_ERROR

    out["protocol"] = PROTOCOL_VERSION
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    return EXIT_OK
