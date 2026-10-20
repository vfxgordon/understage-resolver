//
// Copyright 2026 Gordon Chapman
// SPDX-License-Identifier: Apache-2.0
//
#ifndef ASSETLIBRESOLVER_FETCHER_H
#define ASSETLIBRESOLVER_FETCHER_H

#include "api.h"

#include "pxr/pxr.h"

#include <chrono>
#include <future>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

/// Canonical name of the local directory for version \p num: v001, v014,
/// v1000. Every version spelling (14, v14, v014) maps to this.
ASSETLIBRESOLVER_API
std::string AssetLibFormatVersion(long num);

/// \class AssetLibFetcher
///
/// Fills a local asset library (a cache of immutable version snapshots,
/// <root>/<asset>/vNNN/) from a remote repository such as Perforce.
///
/// The remote is reached through a *backend helper*: an external command,
/// configured per library root in <root>/.assetlib.json, that speaks a
/// small CLI/JSON protocol (docs/backend-protocol.md):
///
/// \code
/// <command...> list-versions <asset>              -> {"versions": [1, 2]}
/// <command...> fetch <asset> <N> <empty-dest-dir>  -> {"source": "..."}
/// \endcode
///
/// Fetching is blocking (ArResolver::Resolve is synchronous) but cheap to
/// call repeatedly:
///  - one fetch per asset version, however many threads ask concurrently;
///  - snapshots are written to a temporary directory and renamed into
///    place, so a partial version is never visible, even across processes;
///  - failures are remembered for a while instead of retried immediately;
///  - published versions are immutable, so a fetched version is never
///    refetched. Only the list of versions (for "latest") expires;
///  - fetched versions are write-protected (files and directories lose
///    their write bits), so a cached copy of a repository revision can't be
///    edited by accident. Changes must go through a new published version.
///
/// All methods are thread-safe.
class AssetLibFetcher
{
public:
    struct Config
    {
        std::vector<std::string> command;
        double timeoutSeconds = 600.0;
        double latestTtlSeconds = 60.0;
        double failureTtlSeconds = 30.0;
    };

    /// Returns the fetcher for \p root, or null if the library has no
    /// backend configured (local-only). Reads <root>/.assetlib.json once.
    ASSETLIBRESOLVER_API
    static std::shared_ptr<AssetLibFetcher> ForRoot(const std::string& root);

    /// True if remote access is disabled by the offline environment
    /// variable; the library then behaves as local-only.
    ASSETLIBRESOLVER_API
    static bool IsOffline();

    /// Highest version the remote has for \p asset, or -1 if none or the
    /// backend failed. Cached for latestTtlSeconds.
    ASSETLIBRESOLVER_API
    long GetRemoteLatest(const std::string& asset);

    /// Makes sure <root>/<asset>/vNNN exists locally, fetching it if
    /// needed. Blocks until done. Returns true if the version is present.
    ASSETLIBRESOLVER_API
    bool EnsureVersion(const std::string& asset, long version);

    const std::string& GetRoot() const { return _root; }
    const Config& GetConfig() const { return _config; }

    AssetLibFetcher(const std::string& root, const Config& config);

private:
    using _Clock = std::chrono::steady_clock;

    bool _Fetch(const std::string& asset, long version,
                const std::string& versionDir);

    const std::string _root;
    const Config _config;

    std::mutex _mutex;
    std::map<std::string, std::shared_future<bool>> _inFlight;
    std::map<std::string, _Clock::time_point> _failures;
    struct _Latest { _Clock::time_point time; long latest; double ttlSeconds; };
    std::map<std::string, _Latest> _latest;
};

PXR_NAMESPACE_CLOSE_SCOPE

#endif
