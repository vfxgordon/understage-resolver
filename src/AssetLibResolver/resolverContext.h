//
// Copyright 2026 Gordon Chapman
// SPDX-License-Identifier: Apache-2.0
//
#ifndef ASSETLIBRESOLVER_RESOLVER_CONTEXT_H
#define ASSETLIBRESOLVER_RESOLVER_CONTEXT_H

#include "api.h"

#include "pxr/pxr.h"
#include "pxr/usd/ar/defineResolverContext.h"

#include <map>
#include <memory>
#include <string>

PXR_NAMESPACE_OPEN_SCOPE

/// \class AssetLibResolverContext
///
/// Resolver context for the \c ustage: URI scheme.
///
/// A context optionally overrides the library root and pins assets to
/// specific versions ("release manifest"). Unpinned assets resolve to the
/// latest version on disk.
///
/// There are two flavours:
///
/// - **Manifest-backed**: constructed from a JSON file path. Identity
///   (equality and hash) is the manifest path, so a stage keeps the same
///   context while the manifest's contents change. All contexts for the
///   same manifest share state; the file is read once, when the first one
///   is created. Call ArResolver::RefreshContext() after editing the file
///   to re-read it and recompose any stage using this context.
///
/// - **Inline**: constructed from a pin map. Immutable; identity is the
///   pins plus root.
///
/// Manifest format:
/// \code
/// {
///     "root":   "/optional/library/root",   // relative to the manifest
///     "assets": { "robots/franka": "v001",
///                 "components/servos/xm430": "v003" }
/// }
/// \endcode
class AssetLibResolverContext
{
public:
    using PinMap = std::map<std::string, std::string>;

    /// Empty context: no pins, library root from the environment.
    ASSETLIBRESOLVER_API
    AssetLibResolverContext();

    /// Manifest-backed context. The path is made absolute; the file is
    /// read immediately. Parse errors are reported via TF_WARN and leave
    /// the context with no pins.
    ASSETLIBRESOLVER_API
    explicit AssetLibResolverContext(const std::string& manifestPath);

    /// Inline context with explicit pins and optional library root.
    ASSETLIBRESOLVER_API
    AssetLibResolverContext(const PinMap& pins,
                            const std::string& libraryRoot = std::string());

    ASSETLIBRESOLVER_API
    const std::string& GetManifestPath() const;

    /// Library root override, or empty to use $ASSET_LIBRARY_ROOT.
    ASSETLIBRESOLVER_API
    std::string GetLibraryRoot() const;

    /// Snapshot of the current pins.
    ASSETLIBRESOLVER_API
    PinMap GetPins() const;

    /// Returns true and fills \p version if \p assetName (the full
    /// category path, e.g. "robots/franka") is pinned.
    ASSETLIBRESOLVER_API
    bool GetPinnedVersion(const std::string& assetName,
                          std::string* version) const;

    /// Re-read the manifest. Returns true if pins or root changed.
    /// No-op returning false for inline contexts.
    ASSETLIBRESOLVER_API
    bool Reload();

    ASSETLIBRESOLVER_API
    std::string GetAsString() const;

    ASSETLIBRESOLVER_API
    bool operator==(const AssetLibResolverContext& rhs) const;
    bool operator!=(const AssetLibResolverContext& rhs) const {
        return !(*this == rhs);
    }
    ASSETLIBRESOLVER_API
    bool operator<(const AssetLibResolverContext& rhs) const;

    ASSETLIBRESOLVER_API
    friend size_t hash_value(const AssetLibResolverContext& ctx);

private:
    struct _Data;
    // Shared by all copies, and by all contexts for the same manifest, so a
    // Reload() through any of them is seen by every bound context.
    std::shared_ptr<_Data> _data;
};

inline std::string
ArGetDebugString(const AssetLibResolverContext& ctx)
{
    return ctx.GetAsString();
}

AR_DECLARE_RESOLVER_CONTEXT(AssetLibResolverContext);

PXR_NAMESPACE_CLOSE_SCOPE

#endif
