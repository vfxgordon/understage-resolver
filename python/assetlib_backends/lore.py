#
# Copyright 2026 Gordon Chapman
# SPDX-License-Identifier: Apache-2.0
#
"""Lore backend (https://github.com/EpicGames/lore).

The repository layout has no version directories; an asset is a directory:

    robots/franka/franka.usda
    robots/franka/geo.usda

Asset version N is the Nth revision (oldest = 1) that changed the asset's
directory, as listed by `lore file history`. Fetching version N exports the
asset's files as of that revision with `lore repository dump` (to list them)
and `lore file write --address` (to write each one), so any number of
versions can be materialized side by side and the helper never syncs a
working tree.

All queries run in a private *bare* instance of the repository (`lore clone
--bare`: metadata only, no working-tree files), created on first use and
shared by every invocation. Lore's own local store caches the fetched
content, keyed by content hash, so unchanged files are shared across
versions.

Tested against Lore v0.10.0. Lore is pre-1.0 and its CLI has no structured
(JSON) output, so the text of `repository dump` and `file history` is parsed
strictly: an unrecognized line is an error, never silently skipped.

Caveat: versions are positional in the asset's history, so rewriting that
history renumbers its versions. Published history must be append-only.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from assetlib_backends._protocol import BackendError, NotFound, main, safe_join

# Exit code `lore` uses for "file not found" (path absent at that revision).
_LORE_NOT_FOUND = 82

# `lore repository dump` tree lines, e.g.
#   franka/meshes/hi/link 2.usdc id 15 parent 13 sibling 0 mode 00 size 3 flags 1 addr <a>
#   franka/meshes/ id 12 parent 6 sibling 8 mode 00 size 6 flags 0 child 14
# Paths may contain spaces, so match from the fixed trailing fields.
_DUMP_NODE = re.compile(
    r"^(?P<path>.+?) id \d+ parent \d+ sibling \d+ mode \S+ size \d+ flags \d+"
    r"(?: child \d+| addr (?P<addr>\S+))?$"
)

_HISTORY_REVISION = re.compile(r"^Revision\s*:\s*(\d+)\s*$")
_HISTORY_SIGNATURE = re.compile(r"^Signature\s*:\s*([0-9a-f]{64})\s*$")


class Lore:
    def __init__(self, url, lore="lore", instance=None, branch=None, root=""):
        if not url.startswith("lore://"):
            raise BackendError(f"--url must be a lore:// repository URL, got {url!r}")
        self.url = url
        self.lore = lore
        self.branch = branch
        self.root = root.strip("/")
        self.instance = Path(instance) if instance else _default_instance(url)

    # --- lore plumbing ------------------------------------------------------

    def _run(self, *args, not_found=None):
        """Run lore in the bare instance; return stdout. Raises NotFound on
        lore's file-not-found exit if `not_found` is given."""
        self._ensure_instance()
        cmd = [self.lore, "--non-interactive", "--no-pager", *args]
        try:
            proc = subprocess.run(cmd, cwd=self.instance, capture_output=True,
                                  text=True, check=False)
        except FileNotFoundError:
            raise BackendError(f"lore client not found: {self.lore!r}") from None
        if proc.returncode == _LORE_NOT_FOUND and not_found:
            raise NotFound(not_found)
        if proc.returncode != 0:
            raise BackendError(
                f"lore {args[0]} {args[1] if len(args) > 1 else ''} failed "
                f"(exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
        return proc.stdout

    def _ensure_instance(self):
        """Create the bare instance on first use. Concurrent helpers may race:
        each clones into its own temp dir and the first rename wins."""
        if (self.instance / ".lore").is_dir():
            return
        self.instance.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.instance.with_name(f"{self.instance.name}.tmp-{os.getpid()}")
        shutil.rmtree(tmp, ignore_errors=True)
        proc = subprocess.run(
            [self.lore, "--non-interactive", "--no-pager", "clone", "--bare",
             self.url, str(tmp)],
            capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            raise BackendError(f"lore clone --bare {self.url} failed: "
                               f"{proc.stderr.strip() or proc.stdout.strip()}")
        try:
            os.rename(tmp, self.instance)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
            if not (self.instance / ".lore").is_dir():
                raise
        else:
            # Lore records instance locations; tell it about the move.
            subprocess.run([self.lore, "--non-interactive", "repository", "update-path"],
                           cwd=self.instance, capture_output=True, check=False)

    def _asset_path(self, asset):
        return f"{self.root}/{asset}" if self.root else asset

    def _revisions(self, asset):
        """[(number, signature)] of revisions that changed the asset, oldest first."""
        args = ["file", "history", self._asset_path(asset), "1000000"]
        if self.branch:
            args += ["--branch", self.branch]
        try:
            out = self._run(*args, not_found=asset)
        except NotFound:
            return []
        revisions, number, records = [], None, 0
        for line in out.splitlines():
            if m := _HISTORY_REVISION.match(line):
                records += 1
                number = int(m.group(1))
            elif (m := _HISTORY_SIGNATURE.match(line)) and number is not None:
                revisions.append((number, m.group(1)))
                number = None
        # Every record must yield (number, signature); a partial parse would
        # silently renumber versions.
        if len(revisions) != records:
            raise BackendError("unexpected `lore file history` output: "
                               f"{records} revisions but {len(revisions)} signatures")
        return sorted(revisions)

    def _files_at(self, asset, signature):
        """{relative path: content address} of the asset at a revision."""
        path = self._asset_path(asset)
        out = self._run("repository", "dump", "--path", path, "--revision", signature,
                        not_found=f"{asset} at {signature}")
        prefix = path.rsplit("/", 1)[-1] + "/"
        files, in_tree = {}, False
        for line in out.splitlines():
            if not in_tree:  # Header (repository, revision); the tree follows.
                in_tree = line.startswith("Tree:")
                continue
            if not line.strip():
                continue
            m = _DUMP_NODE.match(line)
            if not m:
                raise BackendError(f"unexpected `lore repository dump` line: {line!r}")
            node, addr = m.group("path"), m.group("addr")
            if not node.startswith(prefix):
                raise BackendError(f"dump entry outside the asset: {node!r}")
            if addr:
                files[node[len(prefix):]] = addr
        if not in_tree:
            raise BackendError("`lore repository dump` output has no Tree: section")
        return files

    # --- protocol -----------------------------------------------------------

    def list_versions(self, asset):
        return list(range(1, len(self._revisions(asset)) + 1))

    def fetch(self, asset, version, dest):
        revisions = self._revisions(asset)
        if version > len(revisions):
            raise NotFound(f"{asset} version {version} (latest is {len(revisions)})")
        number, signature = revisions[version - 1]
        files = self._files_at(asset, signature)
        if not files:
            raise NotFound(f"{asset} has no files at revision {number}")
        for rel, addr in files.items():
            target = safe_join(dest, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._run("file", "write", "--address", addr, "--output", str(target))
        return f"{self.url}/{self._asset_path(asset)}@{number} ({signature})"


def _default_instance(url):
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    return cache / "assetlib" / "lore" / key


def _add_arguments(parser):
    parser.add_argument("--url", required=True,
                        help="repository URL, e.g. lore://lore.example.com:41337/assets")
    parser.add_argument("--lore", default="lore", help="lore client executable")
    parser.add_argument("--instance",
                        help="bare instance directory (default: under $XDG_CACHE_HOME)")
    parser.add_argument("--branch", help="branch whose history defines versions "
                                         "(default: the instance's current branch)")
    parser.add_argument("--root", default="",
                        help="directory in the repository holding the library")


if __name__ == "__main__":
    sys.exit(main(
        lambda a: Lore(a.url, lore=a.lore, instance=a.instance, branch=a.branch, root=a.root),
        _add_arguments,
    ))
