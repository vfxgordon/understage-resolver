//
// Copyright 2026 Gordon Chapman
// SPDX-License-Identifier: Apache-2.0
//
#include "debugCodes.h"

#include "pxr/base/tf/registryManager.h"

PXR_NAMESPACE_OPEN_SCOPE

TF_REGISTRY_FUNCTION(TfDebug)
{
    TF_DEBUG_ENVIRONMENT_SYMBOL(ASSETLIB_RESOLVER,
        "Print debug output from the asset library URI resolver");
}

PXR_NAMESPACE_CLOSE_SCOPE
