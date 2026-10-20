cmake_minimum_required(VERSION 3.24)
project(AssetLibResolver LANGUAGES CXX)

# Standalone build against an OpenUSD install. For Houdini (building against
# the USD in $HFS), use houdini/CMakeLists.txt instead.

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
# compile_commands.json for IDEs / clangd.
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Point CMAKE_PREFIX_PATH (or pxr_DIR) at an OpenUSD install, e.g.
#   cmake -B build -DCMAKE_PREFIX_PATH=$USD_INSTALL_ROOT
find_package(pxr CONFIG REQUIRED)
find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module)

include(cmake/AssetLibConfig.cmake)

# Install layout, mirroring OpenUSD's own:
#   plugin/usd/plugInfo.json                          (Includes */resources/)
#   plugin/usd/AssetLibResolver.so                    (the resolver)
#   plugin/usd/AssetLibResolver/resources/plugInfo.json
#   lib/python/AssetLibResolver/{__init__.py,_assetLibResolver.so}
set(PLUGIN_DIR plugin/usd)
set(PYTHON_DIR lib/python/AssetLibResolver)

set(PXR_LIB_DIR "${pxr_DIR}/lib")

# --- Resolver plugin -------------------------------------------------------

add_library(AssetLibResolver SHARED ${ASSETLIB_PLUGIN_SOURCES})
set_target_properties(AssetLibResolver PROPERTIES
    PREFIX ""
    INSTALL_RPATH "${PXR_LIB_DIR}"
)
target_compile_definitions(AssetLibResolver PRIVATE
    ASSETLIBRESOLVER_EXPORTS ${ASSETLIB_MFB_DEFINES})
target_include_directories(AssetLibResolver PUBLIC
    ${ASSETLIB_SOURCE_DIR}
    ${ASSETLIB_GENERATED_DIR})
target_link_libraries(AssetLibResolver PUBLIC ar js tf vt)

# --- Python module ---------------------------------------------------------

add_library(_assetLibResolver SHARED ${ASSETLIB_PYMODULE_SOURCES})
set_target_properties(_assetLibResolver PROPERTIES
    PREFIX ""
    # Load the very same resolver library Plug loads, not a second copy.
    INSTALL_RPATH "$ORIGIN/../../../${PLUGIN_DIR};${PXR_LIB_DIR}"
)
target_compile_definitions(_assetLibResolver PRIVATE ${ASSETLIB_MFB_DEFINES})
target_link_libraries(_assetLibResolver PRIVATE AssetLibResolver Python3::Module)

# --- Install ---------------------------------------------------------------

# Relative to Root (plugin/usd/AssetLibResolver), not to resources/.
set(PLUG_INFO_LIBRARY_PATH "../AssetLibResolver.so")
set(PLUG_INFO_RESOURCE_PATH ".")
set(PLUG_INFO_ROOT "..")
configure_file(${ASSETLIB_PLUGINFO_IN}
    ${CMAKE_CURRENT_BINARY_DIR}/plugInfo.json @ONLY)
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/plugInfo.root.json
    "{ \"Includes\": [ \"*/resources/\" ] }\n")

install(TARGETS AssetLibResolver LIBRARY DESTINATION ${PLUGIN_DIR})
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/plugInfo.json
    DESTINATION ${PLUGIN_DIR}/AssetLibResolver/resources)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/plugInfo.root.json
    DESTINATION ${PLUGIN_DIR} RENAME plugInfo.json)

install(TARGETS _assetLibResolver LIBRARY DESTINATION ${PYTHON_DIR})
install(FILES ${ASSETLIB_PYTHON_FILES} DESTINATION ${PYTHON_DIR})

# Backend helpers (run by the resolver as external commands).
install(DIRECTORY ${ASSETLIB_BACKENDS_DIR} DESTINATION lib/python
    FILES_MATCHING PATTERN "*.py")
