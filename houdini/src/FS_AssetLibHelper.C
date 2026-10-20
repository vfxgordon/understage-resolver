//
// Copyright 2026 Gordon Chapman
// SPDX-License-Identifier: Apache-2.0
//
// Houdini file-system helper for asset library URIs (ustage:robots/franka?v=2).
//
// Lets every Houdini file parameter outside Solaris (File SOP, USD Import,
// textures, hou.readFile, ...) read ustage: paths. Resolution goes through
// USD's ArGetResolver(), i.e. the very same AssetLibResolver plugin and
// process-wide state Solaris uses: same version selection, same remote
// fetching, same read-only cache. Nothing is resolved twice or differently.
//
// With no USD stage involved there is no bound resolver context, so the
// resolver uses its default context: $ASSET_LIBRARY_MANIFEST if set, the
// library root from $ASSET_LIBRARY_ROOT.
//
// The library is read-only: writes to ustage: paths are refused (rather than
// falling through to a local file literally named "ustage:...").
//
// Installed into <package>/dso/fs/, where Houdini loads FS helpers from.
//

#include "config.h"

#include <FS/FS_Info.h>
#include <FS/FS_Reader.h>
#include <FS/FS_Utils.h>      // declares installFSHelpers() extern "C"
#include <FS/FS_ReaderStream.h>
#include <FS/FS_Writer.h>
#include <FS/FS_WriterStream.h>
#include <UT/UT_DSOVersion.h>
#include <UT/UT_FileUtil.h>
#include <UT/UT_String.h>
#include <UT/UT_StringArray.h>

#include <pxr/base/plug/registry.h>
#include <pxr/usd/ar/resolver.h>

#include <dlfcn.h>
#include <sys/stat.h>

#include <mutex>
#include <string>

PXR_NAMESPACE_USING_DIRECTIVE

namespace {

constexpr char thePrefix[] = ASSETLIB_URI_SCHEME ":";
constexpr size_t thePrefixLen = sizeof(thePrefix) - 1;

bool
isOurs(const char *source)
{
    return source && strncasecmp(source, thePrefix, thePrefixLen) == 0;
}

// Houdini normally registers our USD plugin itself, from dso/usd_plugins on
// HOUDINI_PATH. This is only a safety net for processes where it hasn't (e.g.
// a standalone HDK program), and it must run lazily, on the first ustage: path:
// touching PlugRegistry from installFSHelpers(), i.e. during Houdini's
// startup, forces USD plugin discovery before Houdini has set USD up and
// leaves the interactive viewport drawing nothing (Houdini 22.0 GUI).
void
ensureUsdPluginRegistered()
{
    static std::once_flag once;
    std::call_once(once, [] {
        PlugRegistry &registry = PlugRegistry::GetInstance();
        if (registry.GetPluginWithName("AssetLibResolver")) {
            return;
        }
        Dl_info info;
        if (!dladdr(reinterpret_cast<void *>(&ensureUsdPluginRegistered),
                    &info) || !info.dli_fname) {
            return;
        }
        // <package>/dso/fs/<this>.so -> <package>/dso/usd_plugins
        UT_String dir(UT_String::ALWAYS_DEEP, info.dli_fname);
        UT_String fsdir, file;
        dir.splitPath(fsdir, file);
        registry.RegisterPlugins(std::string(fsdir.c_str()) + "/../usd_plugins");
    });
}

// Resolve a ustage: URI to a local file (fetching it if the library has a
// remote backend). Empty if it cannot be resolved.
std::string
resolve(const char *source)
{
    ensureUsdPluginRegistered();
    ArResolver &resolver = ArGetResolver();
    return resolver.Resolve(resolver.CreateIdentifier(source)).GetPathString();
}

class AssetLibReadHelper : public FS_ReaderHelper
{
public:
    AssetLibReadHelper() { UTaddAbsolutePathPrefix(thePrefix); }

    FS_ReaderStream *createStream(const char *source,
                                  const UT_Options *) override
    {
        if (!isOurs(source)) {
            return nullptr;
        }
        const std::string path = resolve(source);
        return path.empty() ? nullptr : new FS_ReaderStream(path.c_str());
    }

    // '?' introduces our version query (ustage:asset?v=2), not an index-file
    // section, so claim our paths and never split them. Index-file sections
    // inside library assets are not supported.
    bool splitIndexFileSectionPath(const char *source_section_path,
                                   UT_String &index_file_path,
                                   UT_String &section_name) override
    {
        if (!isOurs(source_section_path)) {
            return false;
        }
        index_file_path.harden(source_section_path);
        section_name.clear();
        return true;
    }

    bool combineIndexFileSectionPath(UT_String &,
                                     const char *,
                                     const char *) override
    {
        return false;
    }
};

class AssetLibWriteHelper : public FS_WriterHelper
{
public:
    AssetLibWriteHelper() { UTaddAbsolutePathPrefix(thePrefix); }

    // Claim our paths with a stream that has no output, so the write fails
    // instead of Houdini creating a local file named "ustage:...". Publishing
    // into the library is done by the pipeline, never by writing a URI.
    FS_WriterStream *createStream(const char *source) override
    {
        return isOurs(source) ? new FS_WriterStream() : nullptr;
    }

    bool canMakeDirectory(const char *) override { return false; }

    bool makeDirectory(const char *, mode_t, bool) override { return false; }
};

class AssetLibInfoHelper : public FS_InfoHelper
{
public:
    AssetLibInfoHelper() { UTaddAbsolutePathPrefix(thePrefix); }

    bool canHandle(const char *source) override { return isOurs(source); }

    bool hasAccess(const char *source, int mode) override
    {
        // Existence or read access only: the library is read-only.
        if (mode & (W_OK | X_OK)) {
            return false;
        }
        return !resolve(source).empty();
    }

    bool getIsDirectory(const char *) override { return false; }

    time_t getModTime(const char *source) override
    {
        struct stat st;
        const std::string path = resolve(source);
        return !path.empty() && stat(path.c_str(), &st) == 0 ? st.st_mtime : 0;
    }

    int64 getSize(const char *source) override
    {
        struct stat st;
        const std::string path = resolve(source);
        return !path.empty() && stat(path.c_str(), &st) == 0 ? st.st_size : 0;
    }

    // The URI has no file extension; report the entry layer's, so Houdini
    // picks the right file format (e.g. .usd vs .usdc).
    UT_String getExtension(const char *source) override
    {
        UT_String ext(UT_String::ALWAYS_DEEP);
        const std::string path = resolve(source);
        if (!path.empty()) {
            ext = UT_String(path.c_str()).fileExtension();
        }
        return ext;
    }

    bool getContents(const char *, UT_StringArray &, UT_StringArray *) override
    {
        return false;
    }
};

} // namespace

void
installFSHelpers()
{
    // Nothing USD-related here: see ensureUsdPluginRegistered().
    new AssetLibReadHelper();
    new AssetLibWriteHelper();
    new AssetLibInfoHelper();
}
