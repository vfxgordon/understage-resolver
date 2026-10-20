#
# Copyright 2026 Gordon Chapman
# SPDX-License-Identifier: Apache-2.0
#
"""Backend helpers for the asset library resolver.

Each module is a command-line helper speaking the protocol in
docs/backend-protocol.md; the resolver runs it as configured in the
library's .assetlib.json, e.g.

    {"backend": {"command": ["python3", "-m", "assetlib_backends.p4",
                             "--port", "ssl:perforce:1666",
                             "--depot-root", "//depot/assets"]}}
"""
