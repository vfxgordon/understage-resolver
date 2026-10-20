#
# Copyright 2026 Gordon Chapman
# SPDX-License-Identifier: Apache-2.0
#
"""Python bindings for the asset library URI resolver.

The resolver itself is used through ``pxr.Ar``; this module exposes its
context type, ``ResolverContext``, for binding release manifests or pins,
and ``GetUriScheme()``, the scheme it was built for (default ``ustage``).
"""

from pxr import Tf

Tf.PreparePythonModule()
del Tf
