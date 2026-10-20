#!/usr/bin/env bash
# Run the resolver test suite against the Houdini package build, using
# Houdini's own Python and USD (no Houdini license needed: only pxr is used).
#
#   HFS=/opt/hfs22.0 houdini/run_tests.sh [pytest args]
#   HFS=/opt/hfs20.5 houdini/run_tests.sh [pytest args]
set -euo pipefail
: "${HFS:?set HFS to the Houdini install, e.g. /opt/hfs22.0}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pyver="$("$HFS/python/bin/python3" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
houdini_ver="$(basename "$(readlink -f "$HFS")" | sed -E 's/^hfs([0-9]+\.[0-9]+).*/\1/')"
pkg="${ASSETLIB_HOUDINI_PACKAGE:-$here/install/houdini$houdini_ver}"

# A clean USD environment: never inherit another USD build's paths (mixing
# USD builds in one process fails in confusing ways).
unset PXR_PLUGINPATH_NAME
export LD_LIBRARY_PATH="$HFS/dsolib:$HFS/python/lib"
export PYTHONPATH="$HFS/python/lib/python${pyver}/site-packages:$HFS/houdini/python${pyver}libs"
export ASSETLIB_PLUGIN_PATH="$pkg/dso/usd_plugins"
export ASSETLIB_PYTHON_PATH="$pkg/python${pyver}libs"
export ASSETLIB_BACKENDS_PATH="$pkg/backends"
export ASSETLIB_HOUDINI_PACKAGE="$pkg"
export ASSETLIB_HYTHON="${ASSETLIB_HYTHON:-$HFS/bin/hython}"
export ASSETLIB_HOUDINI_FS_PROBE="${ASSETLIB_HOUDINI_FS_PROBE:-$here/build/houdini$houdini_ver/fs_helper_probe}"

exec uvx --python "$HFS/python/bin/python3" --with pytest \
    pytest "$here/../tests" -p no:cacheprovider "$@"
