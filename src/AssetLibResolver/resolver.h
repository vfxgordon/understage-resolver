//
// Copyright 2026 Gordon Chapman
// SPDX-License-Identifier: Apache-2.0
//
#ifndef ASSETLIBRESOLVER_RESOLVER_H
#define ASSETLIBRESOLVER_RESOLVER_H

#include "api.h"

#include "resolverContext.h"

#include "pxr/pxr.h"
#include "pxr/usd/ar/resolver.h"

#include <memory>
#include <string>

PXR_NAMESPACE_OPEN_SCOPE

/// \class AssetLibResolver
///
/// URI resolver for versioned assets in an on-disk asset library.
///
/// Registered for the \c ustage: scheme (set at build time by the CMake
/// variable \c ASSETLIB_URI_SCHEME; see config.h.in) alongside, not instead
/// of, the default resolver. Examples here use the default \c ustage:. The library layout is the registry; assets may be grouped into
/// categories, and every asset directory holds only version directories:
///
/// \code
/// <root>/robots/franka/v001/franka.usda          (entry layer + its files)
/// <root>/components/servos/xm430/v002/xm430.usda
/// \endcode
///
/// A URI names an asset, not a file, and resolves to the asset's entry
/// layer \c <leaf>.{usd,usda,usdc} in the selected version:
///
/// \code
/// ustage:<category/.../asset>[?v=vNNN|latest]
/// \endcode
///
/// Version selection, highest priority first:
///   1. an explicit \c ?v=vNNN in the URI (\c ?v=latest skips pins),
///   2. a pin in the bound AssetLibResolverContext (keyed by asset path),
///   3. the highest \c vNNN directory on disk.
///
/// Writing (Sdf.Layer.CreateNew) without an explicit version creates the
/// next version, never overwriting an existing one implicitly.
///
/// **Published-asset rule.** Inside a published asset version, the asset's
/// own files are referenced by relative paths (never versioned), and any
/// other asset must be referenced with an explicit \c ?v=vNNN. An
/// unversioned or \c ?v=latest \c ustage: reference made from a layer under
/// \c <root>/.../vNNN/ is refused with a warning and rewritten to an
/// unresolvable identifier (\c ?error=unversioned-ref-in-published-asset),
/// so it fails composition visibly instead of silently floating.
///
/// Resolved paths are plain filesystem paths. Because Sdf anchors relative
/// asset paths against the referencing layer's resolved path, every
/// relative reference inside a resolved asset stays inside the same version
/// directory, so a version is always internally consistent.
///
/// The library root comes from the context, or else from the environment
/// variable \c ASSET_LIBRARY_ROOT. The default context is created from
/// \c ASSET_LIBRARY_MANIFEST if that is set. Both variable names are
/// build-time configurable alongside the scheme.
class AssetLibResolver final : public ArResolver
{
public:
    ASSETLIBRESOLVER_API
    AssetLibResolver();

    ASSETLIBRESOLVER_API
    ~AssetLibResolver() override;

protected:
    std::string _CreateIdentifier(
        const std::string& assetPath,
        const ArResolvedPath& anchorAssetPath) const override;

    std::string _CreateIdentifierForNewAsset(
        const std::string& assetPath,
        const ArResolvedPath& anchorAssetPath) const override;

    ArResolvedPath _Resolve(
        const std::string& assetPath) const override;

    ArResolvedPath _ResolveForNewAsset(
        const std::string& assetPath) const override;

    ArResolverContext _CreateDefaultContext() const override;

    ArResolverContext _CreateContextFromString(
        const std::string& contextStr) const override;

    void _RefreshContext(const ArResolverContext& context) override;

    bool _IsContextDependentPath(
        const std::string& assetPath) const override;

    std::string _GetExtension(
        const std::string& assetPath) const override;

    ArAssetInfo _GetAssetInfo(
        const std::string& assetPath,
        const ArResolvedPath& resolvedPath) const override;

    ArTimestamp _GetModificationTimestamp(
        const std::string& assetPath,
        const ArResolvedPath& resolvedPath) const override;

    std::shared_ptr<ArAsset> _OpenAsset(
        const ArResolvedPath& resolvedPath) const override;

    std::shared_ptr<ArWritableAsset> _OpenAssetForWrite(
        const ArResolvedPath& resolvedPath,
        WriteMode writeMode) const override;

private:
    // Returns the filesystem path for assetPath and, optionally, the
    // version that was selected. Empty on failure.
    std::string _ResolveImpl(const std::string& assetPath,
                             bool forNewAsset,
                             std::string* selectedVersion) const;

    // The bound context, else the default context; may be null.
    const AssetLibResolverContext* _GetContext() const;

    // Absolute library root from ctx, else $ASSET_LIBRARY_ROOT; may be empty.
    std::string _GetLibraryRoot(const AssetLibResolverContext* ctx) const;

    // True if layerPath is a file inside a published asset version, i.e.
    // under <root>/<category/.../asset>/<vNNN>/.
    bool _IsInsidePublishedAsset(const std::string& layerPath) const;

    // From $ASSET_LIBRARY_MANIFEST; used when no context is bound.
    std::unique_ptr<AssetLibResolverContext> _defaultContext;
};

PXR_NAMESPACE_CLOSE_SCOPE

#endif
