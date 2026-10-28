# Shared by every build of the resolver (standalone OpenUSD, Houdini, ...):
# build-time configuration, the generated config.h, and the source lists.
#
# Sets:
#   ASSETLIB_SOURCE_DIR       src/AssetLibResolver
#   ASSETLIB_GENERATED_DIR    directory holding the generated config.h
#   ASSETLIB_PLUGIN_SOURCES   sources of the resolver plugin library
#   ASSETLIB_PYMODULE_SOURCES sources of the _assetLibResolver Python module
#   ASSETLIB_PYTHON_FILES     pure-Python files of the AssetLibResolver package
#   ASSETLIB_MFB_DEFINES      compile definitions both targets need
#   ASSETLIB_PLUGINFO_IN      plugInfo.json template (configure_file @ONLY with
#                             PLUG_INFO_LIBRARY_PATH/RESOURCE_PATH/ROOT set)

set(ASSETLIB_SOURCE_DIR ${CMAKE_CURRENT_LIST_DIR}/../src/AssetLibResolver)
get_filename_component(ASSETLIB_SOURCE_DIR ${ASSETLIB_SOURCE_DIR} ABSOLUTE)

# --- Configuration -----------------------------------------------------------
# All configuration lives here and is baked into the generated config.h and
# plugInfo.json, e.g.  cmake -B build -DASSETLIB_URI_SCHEME=assetlib

set(ASSETLIB_URI_SCHEME "ustage" CACHE STRING
    "URI scheme to register the resolver for, without the colon")
set(ASSETLIB_ROOT_ENV_VAR "ASSET_LIBRARY_ROOT" CACHE STRING
    "Environment variable holding the asset library root directory")
set(ASSETLIB_MANIFEST_ENV_VAR "ASSET_LIBRARY_MANIFEST" CACHE STRING
    "Environment variable naming the default release manifest")
set(ASSETLIB_OFFLINE_ENV_VAR "ASSET_LIBRARY_OFFLINE" CACHE STRING
    "Environment variable that disables remote fetching when set")
set(ASSETLIB_CONFIG_FILE ".assetlib.json" CACHE STRING
    "Per-library configuration file name, at the library root")
set(ASSETLIB_PROVENANCE_FILE ".assetlib-source.json" CACHE STRING
    "Provenance file written into each fetched or published version directory")
set(ASSETLIB_DRAFT_FILE ".assetlib-draft.json" CACHE STRING
    "Marker file of an unpublished (draft) version directory")

# RFC 3986 scheme syntax; Ar matches schemes case-insensitively, so require
# lowercase to keep identifiers canonical.
if(NOT ASSETLIB_URI_SCHEME MATCHES "^[a-z][a-z0-9+.-]*$")
    message(FATAL_ERROR "ASSETLIB_URI_SCHEME '${ASSETLIB_URI_SCHEME}' must be "
        "a lowercase URI scheme without the colon (e.g. 'lib')")
endif()
foreach(_var ASSETLIB_ROOT_ENV_VAR ASSETLIB_MANIFEST_ENV_VAR
        ASSETLIB_OFFLINE_ENV_VAR)
    if(NOT ${_var} MATCHES "^[A-Za-z_][A-Za-z0-9_]*$")
        message(FATAL_ERROR "${_var} '${${_var}}' is not a valid environment "
            "variable name")
    endif()
endforeach()
foreach(_var ASSETLIB_CONFIG_FILE ASSETLIB_PROVENANCE_FILE ASSETLIB_DRAFT_FILE)
    if(NOT ${_var} MATCHES "^[A-Za-z0-9_.-]+$")
        message(FATAL_ERROR "${_var} '${${_var}}' must be a plain file name")
    endif()
endforeach()
message(STATUS "AssetLibResolver: scheme '${ASSETLIB_URI_SCHEME}:', root "
    "$${ASSETLIB_ROOT_ENV_VAR}, manifest $${ASSETLIB_MANIFEST_ENV_VAR}")

set(ASSETLIB_GENERATED_DIR ${CMAKE_CURRENT_BINARY_DIR}/generated)
configure_file(${ASSETLIB_SOURCE_DIR}/config.h.in
    ${ASSETLIB_GENERATED_DIR}/config.h @ONLY)

# --- Sources -------------------------------------------------------------------

set(ASSETLIB_PLUGIN_SOURCES
    ${ASSETLIB_SOURCE_DIR}/debugCodes.cpp
    ${ASSETLIB_SOURCE_DIR}/fetcher.cpp
    ${ASSETLIB_SOURCE_DIR}/resolver.cpp
    ${ASSETLIB_SOURCE_DIR}/resolverContext.cpp
)
set(ASSETLIB_PYMODULE_SOURCES
    ${ASSETLIB_SOURCE_DIR}/module.cpp
    ${ASSETLIB_SOURCE_DIR}/wrapConfig.cpp
    ${ASSETLIB_SOURCE_DIR}/wrapFetch.cpp
    ${ASSETLIB_SOURCE_DIR}/wrapResolverContext.cpp
)
set(ASSETLIB_PYTHON_FILES
    ${ASSETLIB_SOURCE_DIR}/__init__.py
    ${ASSETLIB_SOURCE_DIR}/prefetch.py
    ${ASSETLIB_SOURCE_DIR}/localize.py
)
set(ASSETLIB_MFB_DEFINES
    MFB_PACKAGE_NAME=assetLibResolver
    MFB_ALT_PACKAGE_NAME=assetLibResolver
    MFB_PACKAGE_MODULE=AssetLibResolver
)
set(ASSETLIB_PLUGINFO_IN ${ASSETLIB_SOURCE_DIR}/plugInfo.json.in)
set(ASSETLIB_BACKENDS_DIR ${CMAKE_CURRENT_LIST_DIR}/../python/assetlib_backends)
get_filename_component(ASSETLIB_BACKENDS_DIR ${ASSETLIB_BACKENDS_DIR} ABSOLUTE)
