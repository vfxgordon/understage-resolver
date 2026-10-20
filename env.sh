# Source this file to use the locally built OpenUSD and this plugin.
# USD_INSTALL_ROOT must point at your OpenUSD install.
if [ -z "${USD_INSTALL_ROOT:-}" ]; then
    echo "env.sh: set USD_INSTALL_ROOT to your OpenUSD install, e.g." \
         "export USD_INSTALL_ROOT=\$HOME/usd/25.11" >&2
    return 1 2>/dev/null || exit 1
fi
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$USD_INSTALL_ROOT/bin:$PATH"
export LD_LIBRARY_PATH="$USD_INSTALL_ROOT/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$_here/install/lib/python:$USD_INSTALL_ROOT/lib/python${PYTHONPATH:+:$PYTHONPATH}"
export PXR_PLUGINPATH_NAME="$_here/install/plugin/usd${PXR_PLUGINPATH_NAME:+:$PXR_PLUGINPATH_NAME}"
unset _here
