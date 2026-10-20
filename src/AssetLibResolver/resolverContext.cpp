//
// Copyright 2026 Gordon Chapman
// SPDX-License-Identifier: Apache-2.0
//
#include "resolverContext.h"

#include "pxr/base/js/json.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/hash.h"
#include "pxr/base/tf/pathUtils.h"
#include "pxr/base/tf/stringUtils.h"

#include <fstream>
#include <mutex>
#include <tuple>
#include <unordered_map>

PXR_NAMESPACE_OPEN_SCOPE

struct AssetLibResolverContext::_Data
{
    std::string manifestPath;   // Immutable after construction.

    mutable std::mutex mutex;   // Guards root and pins.
    std::string root;
    PinMap pins;
};

namespace {

// Parse a manifest file. Returns false (with a warning) on any error.
bool
_ReadManifest(const std::string& path, std::string* root,
              AssetLibResolverContext::PinMap* pins)
{
    std::ifstream in(path);
    if (!in) {
        TF_WARN("AssetLibResolver: cannot open manifest '%s'", path.c_str());
        return false;
    }

    JsParseError err;
    const JsValue doc = JsParseStream(in, &err);
    if (!doc.IsObject()) {
        TF_WARN("AssetLibResolver: manifest '%s' is not a JSON object "
                "(line %u, col %u: %s)", path.c_str(),
                err.line, err.column, err.reason.c_str());
        return false;
    }
    const JsObject& obj = doc.GetJsObject();

    root->clear();
    pins->clear();

    if (auto it = obj.find("root"); it != obj.end()) {
        if (!it->second.IsString()) {
            TF_WARN("AssetLibResolver: manifest '%s': \"root\" must be a string",
                    path.c_str());
            return false;
        }
        std::string r = it->second.GetString();
        if (!r.empty() && !TfIsRelativePath(r)) {
            *root = TfNormPath(r);
        } else if (!r.empty()) {
            *root = TfNormPath(TfStringCatPaths(TfGetPathName(path), r));
        }
    }

    if (auto it = obj.find("assets"); it != obj.end()) {
        if (!it->second.IsObject()) {
            TF_WARN("AssetLibResolver: manifest '%s': \"assets\" must be an "
                    "object", path.c_str());
            return false;
        }
        for (const auto& [name, ver] : it->second.GetJsObject()) {
            if (!ver.IsString()) {
                TF_WARN("AssetLibResolver: manifest '%s': version for '%s' "
                        "must be a string", path.c_str(), name.c_str());
                continue;
            }
            (*pins)[name] = ver.GetString();
        }
    }
    return true;
}

} // anon

AssetLibResolverContext::AssetLibResolverContext()
    : _data(std::make_shared<_Data>())
{
}

AssetLibResolverContext::AssetLibResolverContext(
    const std::string& manifestPath)
{
    // Every context for the same manifest shares one _Data. Identity is the
    // manifest path, so a context constructed anywhere (e.g. to pass to
    // ArResolver::RefreshContext) must see, and update, the same pins as the
    // copy bound to an open stage.
    static std::mutex registryMutex;
    static std::unordered_map<std::string, std::weak_ptr<_Data>> registry;

    const std::string absPath = TfAbsPath(manifestPath);

    std::lock_guard<std::mutex> lock(registryMutex);
    if (auto it = registry.find(absPath); it != registry.end()) {
        if ((_data = it->second.lock())) {
            return;
        }
    }
    _data = std::make_shared<_Data>();
    _data->manifestPath = absPath;
    _ReadManifest(absPath, &_data->root, &_data->pins);
    registry[absPath] = _data;
}

AssetLibResolverContext::AssetLibResolverContext(
    const PinMap& pins, const std::string& libraryRoot)
    : _data(std::make_shared<_Data>())
{
    _data->pins = pins;
    if (!libraryRoot.empty()) {
        _data->root = TfAbsPath(libraryRoot);
    }
}

const std::string&
AssetLibResolverContext::GetManifestPath() const
{
    return _data->manifestPath;
}

std::string
AssetLibResolverContext::GetLibraryRoot() const
{
    std::lock_guard<std::mutex> lock(_data->mutex);
    return _data->root;
}

AssetLibResolverContext::PinMap
AssetLibResolverContext::GetPins() const
{
    std::lock_guard<std::mutex> lock(_data->mutex);
    return _data->pins;
}

bool
AssetLibResolverContext::GetPinnedVersion(
    const std::string& assetName, std::string* version) const
{
    std::lock_guard<std::mutex> lock(_data->mutex);
    auto it = _data->pins.find(assetName);
    if (it == _data->pins.end()) {
        return false;
    }
    *version = it->second;
    return true;
}

bool
AssetLibResolverContext::Reload()
{
    if (_data->manifestPath.empty()) {
        return false;
    }
    std::string root;
    PinMap pins;
    if (!_ReadManifest(_data->manifestPath, &root, &pins)) {
        return false;
    }
    std::lock_guard<std::mutex> lock(_data->mutex);
    if (root == _data->root && pins == _data->pins) {
        return false;
    }
    _data->root = std::move(root);
    _data->pins = std::move(pins);
    return true;
}

std::string
AssetLibResolverContext::GetAsString() const
{
    std::vector<std::string> pins;
    for (const auto& [name, ver] : GetPins()) {
        pins.push_back(name + "=" + ver);
    }
    return TfStringPrintf(
        "AssetLibResolverContext(manifest='%s', root='%s', pins={%s})",
        _data->manifestPath.c_str(), GetLibraryRoot().c_str(),
        TfStringJoin(pins, ", ").c_str());
}

// Identity: manifest-backed contexts are identified by the manifest path
// alone (their contents are mutable via Reload); inline contexts by their
// immutable root and pins. Equality, ordering and hashing must agree.

bool
AssetLibResolverContext::operator==(const AssetLibResolverContext& rhs) const
{
    if (_data == rhs._data) {
        return true;
    }
    if (_data->manifestPath != rhs._data->manifestPath) {
        return false;
    }
    if (!_data->manifestPath.empty()) {
        return true;
    }
    return GetLibraryRoot() == rhs.GetLibraryRoot() &&
           GetPins() == rhs.GetPins();
}

bool
AssetLibResolverContext::operator<(const AssetLibResolverContext& rhs) const
{
    if (_data->manifestPath != rhs._data->manifestPath) {
        return _data->manifestPath < rhs._data->manifestPath;
    }
    if (!_data->manifestPath.empty()) {
        return false;
    }
    return std::make_tuple(GetLibraryRoot(), GetPins()) <
           std::make_tuple(rhs.GetLibraryRoot(), rhs.GetPins());
}

size_t
hash_value(const AssetLibResolverContext& ctx)
{
    if (!ctx._data->manifestPath.empty()) {
        return TfHash()(ctx._data->manifestPath);
    }
    size_t h = TfHash()(ctx.GetLibraryRoot());
    for (const auto& [name, ver] : ctx.GetPins()) {
        h = TfHash::Combine(h, name, ver);
    }
    return h;
}

PXR_NAMESPACE_CLOSE_SCOPE
