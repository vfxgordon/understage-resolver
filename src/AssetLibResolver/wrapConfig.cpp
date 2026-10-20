//
// Copyright 2026 Gordon Chapman
// SPDX-License-Identifier: Apache-2.0
//
#include "pxr/pxr.h"

#include "config.h"

#include "pyCompat.h"

#include <string>

PXR_NAMESPACE_USING_DIRECTIVE

using namespace assetlib_py;

static std::string
_GetUriScheme()
{
    return ASSETLIB_URI_SCHEME;
}

static std::string
_GetRootEnvVar()
{
    return ASSETLIB_ROOT_ENV_VAR;
}

static std::string
_GetManifestEnvVar()
{
    return ASSETLIB_MANIFEST_ENV_VAR;
}

static std::string
_GetOfflineEnvVar()
{
    return ASSETLIB_OFFLINE_ENV_VAR;
}

static std::string
_GetConfigFileName()
{
    return ASSETLIB_CONFIG_FILE;
}

static std::string
_GetProvenanceFileName()
{
    return ASSETLIB_PROVENANCE_FILE;
}

void
wrapConfig()
{
    def("GetUriScheme", &_GetUriScheme,
        "URI scheme this resolver build is registered for (no colon).");
    def("GetRootEnvVar", &_GetRootEnvVar,
        "Environment variable that sets the library root.");
    def("GetManifestEnvVar", &_GetManifestEnvVar,
        "Environment variable that sets the default release manifest.");
    def("GetOfflineEnvVar", &_GetOfflineEnvVar,
        "Environment variable that disables remote fetching.");
    def("GetConfigFileName", &_GetConfigFileName,
        "Per-library configuration file name (at the library root).");
    def("GetProvenanceFileName", &_GetProvenanceFileName,
        "Provenance file written into each fetched version directory.");
}
