#
# Copyright 2026 Gordon Chapman
# SPDX-License-Identifier: Apache-2.0
#
"""Perforce backend.

Depot layout has no version directories; an asset is a directory tree:

    //depot/assets/robots/franka/franka.usda
    //depot/assets/robots/franka/geo.usda

Asset version N is the Nth submitted changelist that touched
<depot-root>/<asset>/... (oldest = 1). Fetching version N writes the asset's
files as of that changelist into the destination with `p4 print`, so any
number of versions can be materialized side by side without a client
workspace (a workspace can only hold one revision of each path).

Uses the p4 command-line client with tagged JSON output (-ztag -Mj); no
P4Python needed. Connection settings not given as options come from the
usual P4PORT/P4USER/P4TICKETS/P4CONFIG environment.

Caveat: versions are positional, so obliterating history in an asset's
depot path renumbers its versions. Published history must be append-only.
"""

import json
import subprocess
import sys

from assetlib_backends._protocol import BackendError, NotFound, main, safe_join

# Actions whose file revision is not present at that change.
_DELETED = {"delete", "move/delete", "purge", "archive"}


class Perforce:
    def __init__(self, depot_root, p4="p4", port=None, user=None):
        if not depot_root.startswith("//"):
            raise BackendError(f"--depot-root must be a depot path, got {depot_root!r}")
        self.depot_root = depot_root.rstrip("/")
        self.base = [p4]
        if port:
            self.base += ["-p", port]
        if user:
            self.base += ["-u", user]

    # --- p4 plumbing --------------------------------------------------------

    def _run(self, *args, tagged=True):
        cmd = self.base + (["-ztag", "-Mj"] if tagged else []) + list(args)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            raise BackendError(f"p4 client not found: {self.base[0]!r}") from None
        if proc.returncode != 0:
            raise BackendError(f"{' '.join(args[:1])} failed: {proc.stderr.strip()}")
        if not tagged:
            return proc.stdout
        records = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        errors = [r for r in records if r.get("code") == "error" or r.get("severity", 0) >= 3]
        if errors:
            raise BackendError("; ".join(str(e.get("data", e)).strip() for e in errors))
        return records

    def _asset_path(self, asset):
        return f"{self.depot_root}/{asset}"

    def _changes(self, asset):
        """Submitted changelists touching the asset, oldest first."""
        records = self._run("changes", "-s", "submitted", f"{self._asset_path(asset)}/...")
        return sorted(int(r["change"]) for r in records if "change" in r)

    # --- protocol -----------------------------------------------------------

    def list_versions(self, asset):
        return list(range(1, len(self._changes(asset)) + 1))

    def fetch(self, asset, version, dest):
        changes = self._changes(asset)
        if version > len(changes):
            raise NotFound(f"{asset} version {version} (latest is {len(changes)})")
        change = changes[version - 1]
        prefix = self._asset_path(asset) + "/"

        files = [
            r for r in self._run("files", f"{prefix}...@{change}")
            if "depotFile" in r and r.get("action") not in _DELETED
        ]
        if not files:
            raise NotFound(f"{asset} has no files at change {change}")
        for f in files:
            depot_file = f["depotFile"]
            if not depot_file.startswith(prefix):
                raise BackendError(f"unexpected file outside the asset: {depot_file}")
            target = safe_join(dest, depot_file[len(prefix):])
            target.parent.mkdir(parents=True, exist_ok=True)
            self._run("print", "-q", "-o", str(target), f"{depot_file}@{change}", tagged=False)
        return f"{prefix}...@{change}"


def _add_arguments(parser):
    parser.add_argument("--depot-root", required=True,
                        help="depot path holding the library, e.g. //depot/assets")
    parser.add_argument("--p4", default="p4", help="p4 client executable")
    parser.add_argument("--port", help="P4PORT (default: environment)")
    parser.add_argument("--user", help="P4USER (default: environment)")


if __name__ == "__main__":
    sys.exit(main(
        lambda a: Perforce(a.depot_root, p4=a.p4, port=a.port, user=a.user),
        _add_arguments,
    ))
