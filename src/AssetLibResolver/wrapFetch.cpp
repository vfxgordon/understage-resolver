//
// Copyright 2026 Gordon Chapman
// SPDX-License-Identifier: Apache-2.0
//
#include "pxr/pxr.h"

#include "fetcher.h"

#include "pxr/base/tf/pyLock.h"
#include "pyCompat.h"
#include "pxr/usd/ar/resolver.h"
#include "pxr/usd/ar/resolverContextBinder.h"

#include <algorithm>
#include <atomic>
#include <string>
#include <thread>
#include <vector>

PXR_NAMESPACE_USING_DIRECTIVE

using namespace assetlib_py;

// Resolves every URI on up to `jobs` threads, each bound to the caller's
// resolver context. Resolution fetches missing versions, so this fills the
// local cache in parallel; concurrent requests for one version share a
// single fetch. Returns {uri: resolvedPath or ""}.
static dict
_Prefetch(const std::vector<std::string>& uris, int jobs)
{
    if (uris.empty()) {
        return dict();
    }
    const ArResolverContext context = ArGetResolver().GetCurrentContext();
    std::vector<std::string> resolved(uris.size());
    {
        TF_PY_ALLOW_THREADS_IN_SCOPE();

        std::atomic<size_t> next{0};
        auto worker = [&]() {
            ArResolverContextBinder binder(context);
            for (size_t i = next++; i < uris.size(); i = next++) {
                resolved[i] = ArGetResolver().Resolve(uris[i]).GetPathString();
            }
        };
        const size_t n = std::clamp<size_t>(jobs, 1, uris.size());
        std::vector<std::thread> threads;
        for (size_t t = 1; t < n; ++t) {
            threads.emplace_back(worker);
        }
        worker();
        for (std::thread& t : threads) {
            t.join();
        }
    }
    dict result;
    for (size_t i = 0; i < uris.size(); ++i) {
        result[uris[i]] = resolved[i];
    }
    return result;
}

static bool
_IsOffline()
{
    return AssetLibFetcher::IsOffline();
}

void
wrapFetch()
{
    def("Prefetch", &_Prefetch, (arg("uris"), arg("jobs") = 8),
        "Resolve (and fetch into the local cache) the given asset URIs in "
        "parallel under the current resolver context. Returns a dict of "
        "uri -> resolved path ('' on failure).");
    def("IsOffline", &_IsOffline,
        "True if remote fetching is disabled by the offline environment "
        "variable.");
}
