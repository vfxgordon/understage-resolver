cmake_minimum_required(VERSION 3.24)
project(AssetLibResolverHoudini LANGUAGES CXX)

# Builds the asset library resolver as a Houdini package, against the USD
# that ships with Houdini ($HFS), which is generally a different version from
# the standalone/Isaac Sim USD build. The result only loads in the Houdini
# version it was built for.
#
#   export HFS=/opt/hfs22.0             # or /opt/hfs20.5, ...
#   cmake -S houdini -B houdini/build/houdini22.0 -G Ninja
#   cmake --build houdini/build/houdini22.0 && cmake --install houdini/build/houdini22.0
#
# One build directory per Houdini version: each is a separate binary.
#
# Package layout (CMAKE_INSTALL_PREFIX, default houdini/install/houdini<X.Y>):
#   dso/usd/AssetLibResolver.so                        Solaris: USD resolver
#   dso/usd_plugins/plugInfo.json                      (Includes */resources/)
#   dso/usd_plugins/AssetLibResolver/resources/plugInfo.json
#   dso/fs/FS_AssetLibHelper.so                        non-Solaris: ustage: in file parms
#   python<ver>libs/AssetLibResolver/                  Python bindings, prefetch, localize
#   backends/assetlib_backends/                        backend helper commands
#   AssetLibResolver.json                              Houdini package file

if(NOT DEFINED ENV{HFS} AND NOT HFS)
    message(FATAL_ERROR "Set HFS (source $HFS/houdini_setup_bash, or "
        "export HFS=/opt/hfsX.Y.Z, or pass -DHFS=...)")
endif()
if(NOT HFS)
    set(HFS $ENV{HFS})
endif()
get_filename_component(HFS ${HFS} REALPATH)
list(APPEND CMAKE_PREFIX_PATH "${HFS}/toolkit/cmake")
find_package(Houdini REQUIRED)

set(HOUDINI_VERSION_DIR "houdini${Houdini_VERSION_MAJOR}.${Houdini_VERSION_MINOR}")
if(CMAKE_INSTALL_PREFIX_INITIALIZED_TO_DEFAULT)
    set(CMAKE_INSTALL_PREFIX "${CMAKE_CURRENT_SOURCE_DIR}/install/${HOUDINI_VERSION_DIR}"
        CACHE PATH "Houdini package directory" FORCE)
endif()
message(STATUS "AssetLibResolver: Houdini ${Houdini_VERSION} at ${HFS}")
message(STATUS "AssetLibResolver: package -> ${CMAKE_INSTALL_PREFIX}")

# No CMAKE_CXX_STANDARD: Houdini's interface flags carry the -std it was
# built with (C++17 for 20.5, C++20 for 22.0), and the two must match.
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

include(${CMAKE_CURRENT_SOURCE_DIR}/../cmake/AssetLibConfig.cmake)

# Houdini's Python (the bindings must match it; python3.13libs etc.).
get_target_property(_hpython Houdini::Dep::python${_houdini_python_version}
    IMPORTED_LOCATION)
set(HOUDINI_PYTHON_DIR "python${_houdini_python_version}libs")

# The USD plugin and the Python module need only USD (and Python), not the
# whole HDK: take Houdini's include paths, flags and defines, and link the
# pxr libraries explicitly. Only the FS helper links against Houdini proper.
add_library(AssetLibHoudiniUsd INTERFACE)
target_include_directories(AssetLibHoudiniUsd SYSTEM INTERFACE
    $<TARGET_PROPERTY:Houdini,INTERFACE_INCLUDE_DIRECTORIES>)
target_compile_options(AssetLibHoudiniUsd INTERFACE
    $<TARGET_PROPERTY:Houdini,INTERFACE_COMPILE_OPTIONS>)
target_compile_definitions(AssetLibHoudiniUsd INTERFACE
    $<TARGET_PROPERTY:Houdini,INTERFACE_COMPILE_DEFINITIONS>)
target_link_libraries(AssetLibHoudiniUsd INTERFACE
    Houdini::Dep::pxr_ar Houdini::Dep::pxr_arch Houdini::Dep::pxr_js
    Houdini::Dep::pxr_plug Houdini::Dep::pxr_tf Houdini::Dep::pxr_vt
    Houdini::Dep::pxr_work Houdini::Dep::tbb)

set(HDSOLIB "${HFS}/dsolib")

# --- Solaris: USD resolver plugin ------------------------------------------------

add_library(AssetLibResolver SHARED ${ASSETLIB_PLUGIN_SOURCES})
set_target_properties(AssetLibResolver PROPERTIES
    PREFIX ""
    INSTALL_RPATH "${HDSOLIB}"
)
target_compile_definitions(AssetLibResolver PRIVATE
    ASSETLIBRESOLVER_EXPORTS ${ASSETLIB_MFB_DEFINES})
target_include_directories(AssetLibResolver PUBLIC
    ${ASSETLIB_SOURCE_DIR} ${ASSETLIB_GENERATED_DIR})
target_link_libraries(AssetLibResolver PUBLIC AssetLibHoudiniUsd)

# --- Python bindings (Houdini's Python) --------------------------------------------

add_library(_assetLibResolver SHARED ${ASSETLIB_PYMODULE_SOURCES})
set_target_properties(_assetLibResolver PROPERTIES
    PREFIX ""
    # Load the same resolver library Plug loads from dso/usd, not a copy.
    INSTALL_RPATH "$ORIGIN/../../dso/usd;${HDSOLIB};${HFS}/python/lib"
)
target_compile_definitions(_assetLibResolver PRIVATE ${ASSETLIB_MFB_DEFINES})
# Boost.Python: USD >= 24.11 has its own (pxr_python); older Houdini USD
# builds use SideFX's hboost_python. See src/AssetLibResolver/pyCompat.h.
if(TARGET Houdini::Dep::pxr_python)
    set(_boost_python Houdini::Dep::pxr_python)
elseif(TARGET Houdini::Dep::hboost_python)
    set(_boost_python Houdini::Dep::hboost_python)
else()
    message(FATAL_ERROR "No Boost.Python target (pxr_python or hboost_python) in this Houdini")
endif()
target_link_libraries(_assetLibResolver PRIVATE
    AssetLibResolver ${_boost_python}
    Houdini::Dep::python${_houdini_python_version})

# --- Non-Solaris: FS helper for ustage: paths in any file parameter ---------------------

add_library(FS_AssetLibHelper SHARED src/FS_AssetLibHelper.C)
set_target_properties(FS_AssetLibHelper PROPERTIES
    PREFIX ""
    CXX_VISIBILITY_PRESET default   # installFSHelpers() must be exported
    INSTALL_RPATH "${HDSOLIB}"
)
target_include_directories(FS_AssetLibHelper PRIVATE ${ASSETLIB_GENERATED_DIR})
target_link_libraries(FS_AssetLibHelper PRIVATE Houdini AssetLibHoudiniUsd)

# --- Install: the Houdini package ------------------------------------------------------

# Root = dso/usd_plugins/AssetLibResolver; the library is in dso/usd.
set(PLUG_INFO_LIBRARY_PATH "../../usd/AssetLibResolver.so")
set(PLUG_INFO_RESOURCE_PATH "resources")
set(PLUG_INFO_ROOT "..")
configure_file(${ASSETLIB_PLUGINFO_IN} ${CMAKE_CURRENT_BINARY_DIR}/plugInfo.json @ONLY)
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/plugInfo.root.json
    "{ \"Includes\": [ \"*/resources/\" ] }\n")

install(TARGETS AssetLibResolver LIBRARY DESTINATION dso/usd)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/plugInfo.json
    DESTINATION dso/usd_plugins/AssetLibResolver/resources)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/plugInfo.root.json
    DESTINATION dso/usd_plugins RENAME plugInfo.json)
install(TARGETS FS_AssetLibHelper LIBRARY DESTINATION dso/fs)
install(TARGETS _assetLibResolver LIBRARY
    DESTINATION ${HOUDINI_PYTHON_DIR}/AssetLibResolver)
install(FILES ${ASSETLIB_PYTHON_FILES}
    DESTINATION ${HOUDINI_PYTHON_DIR}/AssetLibResolver)
install(DIRECTORY ${ASSETLIB_BACKENDS_DIR} DESTINATION backends
    FILES_MATCHING PATTERN "*.py")

# Package file. Optional defaults for the library can be baked in:
#   -DASSETLIB_HOUDINI_LIBRARY_ROOT=/path/to/library
#   -DASSETLIB_HOUDINI_MANIFEST=/path/to/release.json
set(ASSETLIB_HOUDINI_LIBRARY_ROOT "" CACHE PATH
    "Value for $${ASSETLIB_ROOT_ENV_VAR} in the Houdini package (empty: leave unset)")
set(ASSETLIB_HOUDINI_MANIFEST "" CACHE FILEPATH
    "Value for $${ASSETLIB_MANIFEST_ENV_VAR} in the Houdini package (empty: leave unset)")
set(_pkg_env "")
if(ASSETLIB_HOUDINI_LIBRARY_ROOT)
    string(APPEND _pkg_env
        ",\n        { \"${ASSETLIB_ROOT_ENV_VAR}\": \"${ASSETLIB_HOUDINI_LIBRARY_ROOT}\" }")
endif()
if(ASSETLIB_HOUDINI_MANIFEST)
    string(APPEND _pkg_env
        ",\n        { \"${ASSETLIB_MANIFEST_ENV_VAR}\": \"${ASSETLIB_HOUDINI_MANIFEST}\" }")
endif()
configure_file(${CMAKE_CURRENT_SOURCE_DIR}/AssetLibResolver.json.in
    ${CMAKE_CURRENT_BINARY_DIR}/AssetLibResolver.json @ONLY)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/AssetLibResolver.json DESTINATION .)

# --- Test probe for the FS helper (not installed) ---------------------------------------
# A standalone HDK program; see tests/test_houdini_fs.py.

add_executable(fs_helper_probe tests/fs_helper_probe.C)
set_target_properties(fs_helper_probe PROPERTIES BUILD_RPATH "${HDSOLIB}")
target_link_libraries(fs_helper_probe PRIVATE Houdini)
