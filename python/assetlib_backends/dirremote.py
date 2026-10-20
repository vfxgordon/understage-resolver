#
# Copyright 2026 Gordon Chapman
# SPDX-License-Identifier: Apache-2.0
#
"""Reference backend: a plain directory standing in for a remote repository.

Layout of the "remote":  <remote>/<asset>/<N>/...   (N = 1, 2, ...)

Used by the test suite, and as a minimal example of a backend. Test hooks,
all optional, via environment variables:

    ASSETLIB_TEST_LOG    append one line per invocation: "<cmd> <asset> [<N>]"
    ASSETLIB_TEST_DELAY  sleep this many seconds before answering
    ASSETLIB_TEST_FAIL   fail fetches whose "<asset> <N>" contains this text
                         (after writing a partial file, to test cleanup)
"""

import os
import shutil
import sys
import time
from pathlib import Path

from assetlib_backends._protocol import BackendError, NotFound, main


class DirRemote:
    def __init__(self, remote: Path):
        if not remote.is_dir():
            raise BackendError(f"remote directory {remote} does not exist")
        self.remote = remote

    def list_versions(self, asset):
        _hooks("list-versions", asset)
        adir = self.remote / asset
        if not adir.is_dir():
            return []
        return [int(p.name) for p in adir.iterdir() if p.is_dir() and p.name.isdigit()]

    def fetch(self, asset, version, dest):
        _hooks("fetch", asset, version)
        src = self.remote / asset / str(version)
        if not src.is_dir():
            raise NotFound(f"{asset} version {version}")
        fail = os.environ.get("ASSETLIB_TEST_FAIL")
        if fail and fail in f"{asset} {version}":
            (dest / "partial.usda").write_text("#usda 1.0\n")
            raise BackendError(f"simulated failure fetching {asset} {version}")
        shutil.copytree(src, dest, dirs_exist_ok=True)
        return f"{src}"


def _hooks(*words):
    log = os.environ.get("ASSETLIB_TEST_LOG")
    if log:
        with open(log, "a") as f:
            f.write(" ".join(str(w) for w in words) + "\n")
    delay = os.environ.get("ASSETLIB_TEST_DELAY")
    if delay:
        time.sleep(float(delay))


def _add_arguments(parser):
    parser.add_argument("--remote", type=Path, required=True)


if __name__ == "__main__":
    sys.exit(main(lambda args: DirRemote(args.remote), _add_arguments))
