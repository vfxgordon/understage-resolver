//
// Copyright 2026 Gordon Chapman
// SPDX-License-Identifier: Apache-2.0
//
#include "resolver.h"
#include "config.h"
#include "fetcher.h"
#include "resolverContext.h"
#include "debugCodes.h"

#include "pxr/base/tf/fileUtils.h"
#include "pxr/base/tf/getenv.h"
#include "pxr/base/tf/pathUtils.h"
#include "pxr/base/tf/stringUtils.h"
#include "pxr/base/vt/dictionary.h"
#include "pxr/usd/ar/assetInfo.h"
#include "pxr/usd/ar/defineResolver.h"
#include "pxr/usd/ar/filesystemAsset.h"
#include "pxr/usd/ar/filesystemWritableAsset.h"
#include "pxr/usd/ar/notice.h"

#include <cctype>
#include <filesystem>

PXR_NAMESPACE_OPEN_SCOPE

AR_DEFINE_RESOLVER(AssetLibResolver, ArResolver);

namespace {

constexpr char _scheme[] = ASSETLIB_URI_SCHEME ":";
constexpr size_t _schemeLen = sizeof(_scheme) - 1;
constexpr char _latest[] = "latest";
constexpr char _refusedQuery[] = "error=unversioned-ref-in-published-asset";

// Entry-layer extensions, in probe order.
const char* const _entryExtensions[] = { "usd", "usda", "usdc" };
constexpr char _newEntryExtension[] = "usda";

bool
_IsLibUri(const std::string& path)
{
    // Schemes are case-insensitive.
    return path.size() >= _schemeLen &&
        TfStringToLowerAscii(path.substr(0, _schemeLen)) == _scheme;
}

bool
_IsValidToken(const std::string& s)
{
    if (s.empty() || s == "." || s == "..") {
        return false;
    }
    for (const char c : s) {
        if (!(std::isalnum(static_cast<unsigned char>(c)) ||
              c == '_' || c == '-' || c == '.')) {
            return false;
        }
    }
    return true;
}

// "v" followed by one or more digits: the shape of a version directory.
bool
_IsVersionToken(const std::string& s)
{
    return s.size() >= 2 && s[0] == 'v' &&
        s.find_first_not_of("0123456789", 1) == std::string::npos;
}

// Parses a version spelling (14, v14, v014) into its number; -1 if invalid.
long
_ParseVersionNumber(const std::string& s)
{
    const std::string digits = (!s.empty() && s[0] == 'v') ? s.substr(1) : s;
    if (digits.empty() || digits.size() > 9 ||
        digits.find_first_not_of("0123456789") != std::string::npos) {
        return -1;
    }
    const long num = std::stol(digits);
    return num >= 1 ? num : -1;
}

// Parsed form of ustage:<category/.../asset>[?v=<version>]
struct _LibUri
{
    std::string asset;      // e.g. "components/servos/xm430"
    std::string version;    // Empty when not given in the URI.

    std::string Leaf() const {
        return TfGetBaseName(asset);
    }

    bool IsPinnedInUri() const {
        return !version.empty() && version != _latest;
    }

    std::string ToString() const {
        std::string s = _scheme + asset;
        if (!version.empty()) {
            s += "?v=" + version;
        }
        return s;
    }
};

bool
_ParseLibUri(const std::string& uri, _LibUri* out, std::string* whyNot)
{
    std::string rest = uri.substr(_schemeLen);

    std::string query;
    if (const size_t q = rest.find('?'); q != std::string::npos) {
        query = rest.substr(q + 1);
        rest.resize(q);
    }

    // Canonicalize the asset path: drop empty and "." segments (so ustage:/x,
    // ustage://x and a//b are accepted), reject anything that could escape the
    // library or be confused with a version directory.
    std::vector<std::string> segments;
    for (const std::string& seg : TfStringSplit(rest, "/")) {
        if (seg.empty() || seg == ".") {
            continue;
        }
        if (!_IsValidToken(seg)) {
            *whyNot = "invalid asset path segment '" + seg + "'";
            return false;
        }
        if (_IsVersionToken(seg)) {
            *whyNot = "asset path segment '" + seg + "' looks like a "
                "version; use ?v=" + seg;
            return false;
        }
        segments.push_back(seg);
    }
    if (segments.empty()) {
        *whyNot = std::string("expected ") + _scheme + "<category/.../asset>";
        return false;
    }
    out->asset = TfStringJoin(segments, "/");

    out->version.clear();
    if (!query.empty()) {
        if (!TfStringStartsWith(query, "v=")) {
            *whyNot = "unsupported query '" + query + "' (only v=)";
            return false;
        }
        const std::string version = query.substr(2);
        if (version == _latest) {
            out->version = version;
        } else if (const long num = _ParseVersionNumber(version); num > 0) {
            // Canonical spelling, so ?v=14, ?v=v14 and ?v=v014 are the
            // same identifier and the same cache directory.
            out->version = AssetLibFormatVersion(num);
        } else {
            *whyNot = "invalid version '" + version +
                "' (expected N, vN or latest; versions start at 1)";
            return false;
        }
    }
    return true;
}

// Scans assetDir for version directories in canonical form (v001, v014,
// v1000). Returns the highest version number, or -1 if there are none.
long
_FindLatestVersion(const std::string& assetDir)
{
    namespace fs = std::filesystem;
    std::error_code ec;
    long best = -1;
    for (const auto& entry : fs::directory_iterator(assetDir, ec)) {
        const std::string name = entry.path().filename().string();
        const long num = _IsVersionToken(name) ? _ParseVersionNumber(name) : -1;
        if (num > 0 && name == AssetLibFormatVersion(num) &&
            entry.is_directory(ec)) {
            best = std::max(best, num);
        }
    }
    return best;
}

// Returns the entry layer <leaf>.<ext> in versionDir. Empty if there is
// none, or if more than one extension exists (ambiguous). With forNewAsset,
// a missing entry layer defaults to <leaf>.usda.
std::string
_FindEntryLayer(const std::string& versionDir, const std::string& leaf,
                bool forNewAsset)
{
    std::vector<std::string> found;
    for (const char* ext : _entryExtensions) {
        const std::string path =
            TfStringCatPaths(versionDir, leaf + "." + ext);
        if (TfIsFile(path, /* resolveSymlinks = */ true)) {
            found.push_back(path);
        }
    }
    if (found.size() > 1) {
        TF_WARN("AssetLibResolver: ambiguous entry layer in '%s': %s",
                versionDir.c_str(), TfStringJoin(found, ", ").c_str());
        return std::string();
    }
    if (found.size() == 1) {
        return found[0];
    }
    if (forNewAsset) {
        return TfStringCatPaths(
            versionDir, leaf + "." + _newEntryExtension);
    }
    return std::string();
}

} // anon

AssetLibResolver::AssetLibResolver()
{
    const std::string manifest = TfGetenv(ASSETLIB_MANIFEST_ENV_VAR);
    if (!manifest.empty()) {
        _defaultContext =
            std::make_unique<AssetLibResolverContext>(manifest);
    }
}

AssetLibResolver::~AssetLibResolver() = default;

const AssetLibResolverContext*
AssetLibResolver::_GetContext() const
{
    // With nothing bound (e.g. UsdStage::Open opening its root layer, which
    // happens before the stage's context exists) behave as if the default
    // context were bound.
    const AssetLibResolverContext* ctx =
        _GetCurrentContextObject<AssetLibResolverContext>();
    return ctx ? ctx : _defaultContext.get();
}

std::string
AssetLibResolver::_GetLibraryRoot(const AssetLibResolverContext* ctx) const
{
    std::string root = ctx ? ctx->GetLibraryRoot() : std::string();
    if (root.empty()) {
        root = TfGetenv(ASSETLIB_ROOT_ENV_VAR);
    }
    return root.empty() ? root : TfNormPath(TfAbsPath(root));
}

bool
AssetLibResolver::_IsInsidePublishedAsset(const std::string& layerPath) const
{
    const std::string root = _GetLibraryRoot(_GetContext());
    if (root.empty() || layerPath.empty() || _IsLibUri(layerPath)) {
        return false;
    }
    const std::string path = TfNormPath(TfAbsPath(layerPath));
    if (!TfStringStartsWith(path, root + "/")) {
        return false;
    }
    // Published files live under <root>/<category/.../asset>/<vNNN>/...
    for (const std::string& seg :
             TfStringSplit(path.substr(root.size() + 1), "/")) {
        if (_IsVersionToken(seg)) {
            return true;
        }
    }
    return false;
}

std::string
AssetLibResolver::_CreateIdentifier(
    const std::string& assetPath,
    const ArResolvedPath& anchorAssetPath) const
{
    TF_DEBUG(ASSETLIB_RESOLVER).Msg(
        "AssetLibResolver::_CreateIdentifier('%s', '%s')\n",
        assetPath.c_str(), anchorAssetPath.GetPathString().c_str());

    _LibUri uri;
    std::string whyNot;
    if (!_IsLibUri(assetPath) || !_ParseLibUri(assetPath, &uri, &whyNot)) {
        // Leave it to fail resolution with the original spelling.
        return assetPath;
    }

    // Published-asset rule: a reference made from inside a published asset
    // version must name an explicit version of the asset it depends on.
    // Only relative paths (to the asset's own files) may be versionless.
    // Refuse rather than silently float to a pin or to latest.
    //
    // The refusal must be a non-empty identifier: an empty asset path in a
    // reference or payload means an *internal* reference to the layer's own
    // defaultPrim. Instead return one that can never resolve (the query is
    // rejected by _ParseLibUri), so Pcp reports an ordinary unresolved-asset
    // composition error whose path explains why.
    const std::string& anchor = anchorAssetPath.GetPathString();
    if (!uri.IsPinnedInUri() && _IsInsidePublishedAsset(anchor)) {
        TF_WARN("AssetLibResolver: refusing unversioned reference @%s@ in "
                "published layer '%s': references to other assets must "
                "specify ?v=vNNN", assetPath.c_str(), anchor.c_str());
        return _scheme + uri.asset + "?" + _refusedQuery;
    }

    return uri.ToString();
}

std::string
AssetLibResolver::_CreateIdentifierForNewAsset(
    const std::string& assetPath,
    const ArResolvedPath& anchorAssetPath) const
{
    return _CreateIdentifier(assetPath, anchorAssetPath);
}

std::string
AssetLibResolver::_ResolveImpl(
    const std::string& assetPath,
    bool forNewAsset,
    std::string* selectedVersion) const
{
    _LibUri uri;
    std::string whyNot;
    if (!_ParseLibUri(assetPath, &uri, &whyNot)) {
        TF_DEBUG(ASSETLIB_RESOLVER).Msg(
            "  invalid URI '%s': %s\n", assetPath.c_str(),
            whyNot.c_str());
        return std::string();
    }

    const AssetLibResolverContext* ctx = _GetContext();
    const std::string root = _GetLibraryRoot(ctx);
    if (root.empty()) {
        TF_DEBUG(ASSETLIB_RESOLVER).Msg(
            "  no library root (bind a context or set "
            ASSETLIB_ROOT_ENV_VAR ")\n");
        return std::string();
    }
    const std::string assetDir = TfStringCatPaths(root, uri.asset);

    // Null for a local-only library (no backend configured).
    const std::shared_ptr<AssetLibFetcher> fetcher =
        AssetLibFetcher::ForRoot(root);

    // Version precedence: explicit ?v= > context pin > latest. Latest
    // considers the remote too, so an unfetched newer version wins. A new
    // asset without an explicit version goes into the *next* version so
    // that an existing published version is never overwritten implicitly.
    long versionNum = uri.version == _latest
        ? -1 : _ParseVersionNumber(uri.version);
    if (versionNum < 0 && uri.version.empty() && ctx) {
        std::string pinned;
        if (ctx->GetPinnedVersion(uri.asset, &pinned)) {
            versionNum = _ParseVersionNumber(pinned);
            if (versionNum < 0) {
                TF_WARN("AssetLibResolver: invalid pinned version '%s' for "
                        "'%s'", pinned.c_str(), uri.asset.c_str());
                return std::string();
            }
        }
    }
    if (versionNum < 0) {
        long latest = _FindLatestVersion(assetDir);
        if (fetcher) {
            latest = std::max(latest, fetcher->GetRemoteLatest(uri.asset));
        }
        versionNum = forNewAsset ? std::max(latest, 0L) + 1 : latest;
    }
    if (versionNum < 1) {
        TF_DEBUG(ASSETLIB_RESOLVER).Msg(
            "  no versions of '%s' under '%s'\n",
            uri.asset.c_str(), root.c_str());
        return std::string();
    }
    const std::string version = AssetLibFormatVersion(versionNum);

    // Bring the version into the local cache if it isn't there. Blocking:
    // composition needs the answer now. Concurrent requests for the same
    // version share one fetch.
    if (fetcher && !forNewAsset) {
        fetcher->EnsureVersion(uri.asset, versionNum);
    }

    // A version fetched from the repository is a read-only copy of a
    // published revision; never hand it out as a place to write.
    if (forNewAsset && TfIsFile(TfStringCatPaths(
            TfStringCatPaths(assetDir, version), ASSETLIB_PROVENANCE_FILE))) {
        TF_WARN("AssetLibResolver: refusing to write into %s %s: it is a "
                "read-only copy of a published revision. Publish a new "
                "version instead (omit ?v= to target the next one).",
                uri.asset.c_str(), version.c_str());
        return std::string();
    }

    const std::string path = _FindEntryLayer(
        TfStringCatPaths(assetDir, version), uri.Leaf(), forNewAsset);
    if (path.empty()) {
        TF_DEBUG(ASSETLIB_RESOLVER).Msg(
            "  '%s': no entry layer %s.{usd,usda,usdc} in %s/%s\n",
            assetPath.c_str(), uri.Leaf().c_str(), assetDir.c_str(),
            version.c_str());
        return std::string();
    }

    TF_DEBUG(ASSETLIB_RESOLVER).Msg(
        "  '%s' -> '%s'\n", assetPath.c_str(), path.c_str());
    if (selectedVersion) {
        *selectedVersion = version;
    }
    return path;
}

ArResolvedPath
AssetLibResolver::_Resolve(const std::string& assetPath) const
{
    TF_DEBUG(ASSETLIB_RESOLVER).Msg(
        "AssetLibResolver::_Resolve('%s')\n", assetPath.c_str());
    return ArResolvedPath(_ResolveImpl(assetPath, false, nullptr));
}

ArResolvedPath
AssetLibResolver::_ResolveForNewAsset(const std::string& assetPath) const
{
    TF_DEBUG(ASSETLIB_RESOLVER).Msg(
        "AssetLibResolver::_ResolveForNewAsset('%s')\n", assetPath.c_str());
    return ArResolvedPath(_ResolveImpl(assetPath, true, nullptr));
}

ArResolverContext
AssetLibResolver::_CreateDefaultContext() const
{
    return _defaultContext
        ? ArResolverContext(*_defaultContext) : ArResolverContext();
}

ArResolverContext
AssetLibResolver::_CreateContextFromString(
    const std::string& contextStr) const
{
    // The string form of a context is a manifest path.
    if (contextStr.empty()) {
        return ArResolverContext();
    }
    return ArResolverContext(AssetLibResolverContext(contextStr));
}

void
AssetLibResolver::_RefreshContext(const ArResolverContext& context)
{
    const AssetLibResolverContext* ctx =
        context.Get<AssetLibResolverContext>();
    if (!ctx) {
        return;
    }
    // Reload mutates state shared by every copy of this context, including
    // the one held by any stage it is bound to.
    AssetLibResolverContext shared = *ctx;
    if (shared.Reload()) {
        TF_DEBUG(ASSETLIB_RESOLVER).Msg(
            "AssetLibResolver::_RefreshContext: %s changed\n",
            shared.GetAsString().c_str());
        ArNotice::ResolverChanged(shared).Send();
    }
}

bool
AssetLibResolver::_IsContextDependentPath(const std::string& assetPath) const
{
    // The bound context can change both the library root and the version,
    // so every ustage: path depends on it.
    return _IsLibUri(assetPath);
}

std::string
AssetLibResolver::_GetExtension(const std::string& assetPath) const
{
    // Sdf chooses the file format from this, before resolving, and a ustage:
    // URI names an asset rather than a file. Report the entry layer's
    // extension; a not-yet-existing asset will be created as .usda.
    _LibUri uri;
    std::string whyNot;
    if (!_ParseLibUri(assetPath, &uri, &whyNot)) {
        return std::string();
    }
    const std::string path = _ResolveImpl(assetPath, false, nullptr);
    return path.empty() ? std::string(_newEntryExtension)
                        : TfGetExtension(path);
}

ArAssetInfo
AssetLibResolver::_GetAssetInfo(
    const std::string& assetPath,
    const ArResolvedPath& resolvedPath) const
{
    ArAssetInfo info;
    _LibUri uri;
    std::string whyNot;
    if (!_ParseLibUri(assetPath, &uri, &whyNot)) {
        return info;
    }
    std::string version;
    _ResolveImpl(assetPath, false, &version);

    info.assetName = uri.asset;
    info.version = version;

    VtDictionary resolverInfo;
    resolverInfo["requestedVersion"] = VtValue(uri.version);
    info.resolverInfo = VtValue(resolverInfo);
    return info;
}

ArTimestamp
AssetLibResolver::_GetModificationTimestamp(
    const std::string& assetPath,
    const ArResolvedPath& resolvedPath) const
{
    return ArFilesystemAsset::GetModificationTimestamp(resolvedPath);
}

std::shared_ptr<ArAsset>
AssetLibResolver::_OpenAsset(const ArResolvedPath& resolvedPath) const
{
    return ArFilesystemAsset::Open(resolvedPath);
}

std::shared_ptr<ArWritableAsset>
AssetLibResolver::_OpenAssetForWrite(
    const ArResolvedPath& resolvedPath,
    WriteMode writeMode) const
{
    return ArFilesystemWritableAsset::Create(resolvedPath, writeMode);
}

PXR_NAMESPACE_CLOSE_SCOPE
