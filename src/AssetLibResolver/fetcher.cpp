//
// Copyright 2026 Gordon Chapman
// SPDX-License-Identifier: Apache-2.0
//
#include "fetcher.h"
#include "config.h"
#include "debugCodes.h"

#include "pxr/base/js/json.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/fileUtils.h"
#include "pxr/base/tf/getenv.h"
#include "pxr/base/tf/pathUtils.h"
#include "pxr/base/tf/stringUtils.h"

#include <atomic>
#include <cerrno>
#include <filesystem>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <fstream>

#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

extern char** environ;

PXR_NAMESPACE_OPEN_SCOPE

std::string
AssetLibFormatVersion(long num)
{
    return TfStringPrintf("v%03ld", num);
}

namespace {

// --- Helper process ---------------------------------------------------------

struct _HelperResult
{
    int exitCode = -1;
    bool timedOut = false;
    std::string out;
    std::string err;
    std::string spawnError;
};

std::string
_Quote(const std::vector<std::string>& argv)
{
    std::vector<std::string> quoted;
    for (const std::string& a : argv) {
        quoted.push_back(a.find_first_of(" \t'\"") == std::string::npos
            ? a : "'" + a + "'");
    }
    return TfStringJoin(quoted, " ");
}

// Runs argv with stdin at /dev/null, capturing stdout and stderr. The child
// gets its own process group so a timeout kills the helper *and* anything it
// started (e.g. p4).
_HelperResult
_RunHelper(const std::vector<std::string>& argv, double timeoutSeconds)
{
    _HelperResult result;

    int outPipe[2], errPipe[2];
    if (pipe2(outPipe, O_CLOEXEC) != 0) {
        result.spawnError = strerror(errno);
        return result;
    }
    if (pipe2(errPipe, O_CLOEXEC) != 0) {
        result.spawnError = strerror(errno);
        close(outPipe[0]); close(outPipe[1]);
        return result;
    }

    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0);
    posix_spawn_file_actions_adddup2(&actions, outPipe[1], 1);
    posix_spawn_file_actions_adddup2(&actions, errPipe[1], 2);

    posix_spawnattr_t attr;
    posix_spawnattr_init(&attr);
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETPGROUP);
    posix_spawnattr_setpgroup(&attr, 0);

    std::vector<char*> args;
    for (const std::string& a : argv) {
        args.push_back(const_cast<char*>(a.c_str()));
    }
    args.push_back(nullptr);

    pid_t pid = 0;
    const int rc = posix_spawnp(
        &pid, args[0], &actions, &attr, args.data(), environ);
    posix_spawn_file_actions_destroy(&actions);
    posix_spawnattr_destroy(&attr);
    close(outPipe[1]);
    close(errPipe[1]);

    if (rc != 0) {
        result.spawnError = strerror(rc);
        close(outPipe[0]);
        close(errPipe[0]);
        return result;
    }

    using Clock = std::chrono::steady_clock;
    const auto deadline = Clock::now() +
        std::chrono::milliseconds(static_cast<long>(timeoutSeconds * 1000));

    pollfd fds[2] = { { outPipe[0], POLLIN, 0 }, { errPipe[0], POLLIN, 0 } };
    std::string* sinks[2] = { &result.out, &result.err };
    char buf[65536];

    while (fds[0].fd >= 0 || fds[1].fd >= 0) {
        const auto remaining = std::chrono::duration_cast<
            std::chrono::milliseconds>(deadline - Clock::now()).count();
        if (remaining <= 0) {
            result.timedOut = true;
            break;
        }
        const int n = poll(fds, 2, static_cast<int>(remaining));
        if (n < 0 && errno != EINTR) {
            break;
        }
        for (int i = 0; i < 2; ++i) {
            if (fds[i].fd < 0 || !(fds[i].revents & (POLLIN | POLLHUP))) {
                continue;
            }
            const ssize_t got = read(fds[i].fd, buf, sizeof(buf));
            if (got > 0) {
                sinks[i]->append(buf, got);
            } else if (got == 0 || errno != EINTR) {
                close(fds[i].fd);
                fds[i].fd = -1;
            }
        }
    }
    for (pollfd& p : fds) {
        if (p.fd >= 0) {
            close(p.fd);
        }
    }

    int status = 0;
    if (result.timedOut) {
        kill(-pid, SIGTERM);
        const auto grace = Clock::now() + std::chrono::seconds(2);
        while (waitpid(pid, &status, WNOHANG) == 0) {
            if (Clock::now() > grace) {
                kill(-pid, SIGKILL);
                waitpid(pid, &status, 0);
                break;
            }
            usleep(20000);
        }
    } else {
        while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
    }
    result.exitCode = WIFEXITED(status) ? WEXITSTATUS(status)
                    : WIFSIGNALED(status) ? 128 + WTERMSIG(status) : -1;
    return result;
}

std::string
_Describe(const _HelperResult& r, double timeoutSeconds)
{
    if (!r.spawnError.empty()) {
        return "could not start backend: " + r.spawnError;
    }
    if (r.timedOut) {
        return TfStringPrintf("backend timed out after %gs", timeoutSeconds);
    }
    std::string err = TfStringTrim(r.err);
    if (err.size() > 2000) {
        err = "..." + err.substr(err.size() - 2000);
    }
    return TfStringPrintf("backend exited with status %d%s%s", r.exitCode,
                          err.empty() ? "" : ": ", err.c_str());
}

// --- Configuration ----------------------------------------------------------

bool
_GetSeconds(const JsObject& obj, const char* key, double* out,
            const std::string& path)
{
    auto it = obj.find(key);
    if (it == obj.end()) {
        return true;
    }
    if (it->second.IsReal()) {
        *out = it->second.GetReal();
    } else if (it->second.IsInt()) {
        *out = static_cast<double>(it->second.GetInt());
    } else {
        TF_WARN("AssetLibResolver: %s: \"%s\" must be a number",
                path.c_str(), key);
        return false;
    }
    return true;
}

// Returns false if there is no usable backend configuration.
bool
_ReadConfig(const std::string& path, AssetLibFetcher::Config* config)
{
    std::ifstream in(path);
    if (!in) {
        return false;   // No file: a local-only library.
    }
    JsParseError err;
    const JsValue doc = JsParseStream(in, &err);
    if (!doc.IsObject()) {
        TF_WARN("AssetLibResolver: %s is not a JSON object (line %u, col %u: "
                "%s); treating library as local-only", path.c_str(),
                err.line, err.column, err.reason.c_str());
        return false;
    }
    const JsObject& obj = doc.GetJsObject();
    auto backendIt = obj.find("backend");
    if (backendIt == obj.end()) {
        return false;
    }
    if (!backendIt->second.IsObject()) {
        TF_WARN("AssetLibResolver: %s: \"backend\" must be an object",
                path.c_str());
        return false;
    }
    const JsObject& backend = backendIt->second.GetJsObject();

    auto cmdIt = backend.find("command");
    if (cmdIt == backend.end() || !cmdIt->second.IsArray() ||
        cmdIt->second.GetJsArray().empty()) {
        TF_WARN("AssetLibResolver: %s: \"backend.command\" must be a "
                "non-empty array of strings", path.c_str());
        return false;
    }
    for (const JsValue& v : cmdIt->second.GetJsArray()) {
        if (!v.IsString()) {
            TF_WARN("AssetLibResolver: %s: \"backend.command\" must contain "
                    "only strings", path.c_str());
            return false;
        }
        config->command.push_back(v.GetString());
    }
    return _GetSeconds(backend, "timeout_s", &config->timeoutSeconds, path) &&
        _GetSeconds(backend, "latest_ttl_s", &config->latestTtlSeconds, path) &&
        _GetSeconds(backend, "failure_ttl_s", &config->failureTtlSeconds,
                    path);
}

// Removes every write permission bit from dir, its subdirectories and its
// files. Read-only directories also stop files from being added, deleted or
// renamed inside them, which blocks "write a temp file and rename it over
// the original" saves (Sdf's TfSafeOutputFile) as well as in-place edits.
// Symlinks are left alone (Linux can't chmod a link itself).
bool
_WriteProtect(const std::string& dir, std::string* whyNot)
{
    namespace fs = std::filesystem;
    constexpr fs::perms writeBits =
        fs::perms::owner_write | fs::perms::group_write |
        fs::perms::others_write;
    std::error_code ec;
    std::vector<fs::path> paths{ fs::path(dir) };
    for (auto it = fs::recursive_directory_iterator(dir, ec);
         !ec && it != fs::recursive_directory_iterator(); it.increment(ec)) {
        if (!it->is_symlink(ec)) {
            paths.push_back(it->path());
        }
    }
    if (ec) {
        *whyNot = ec.message();
        return false;
    }
    // Children first: a directory must stay writable until its contents
    // are done (chmod itself doesn't need it, but this keeps the order safe
    // regardless of filesystem semantics).
    for (auto p = paths.rbegin(); p != paths.rend(); ++p) {
        fs::permissions(*p, writeBits, fs::perm_options::remove, ec);
        if (ec) {
            *whyNot = p->string() + ": " + ec.message();
            return false;
        }
    }
    return true;
}

// Undoes _WriteProtect (owner write only) so a directory can be removed.
void
_MakeWritable(const std::string& dir)
{
    namespace fs = std::filesystem;
    std::error_code ec;
    fs::permissions(dir, fs::perms::owner_write, fs::perm_options::add, ec);
    for (auto it = fs::recursive_directory_iterator(dir, ec);
         !ec && it != fs::recursive_directory_iterator(); it.increment(ec)) {
        if (!it->is_symlink(ec)) {
            fs::permissions(it->path(), fs::perms::owner_write,
                            fs::perm_options::add, ec);
        }
    }
}

bool
_Expired(std::chrono::steady_clock::time_point since, double ttlSeconds)
{
    return std::chrono::steady_clock::now() - since >=
        std::chrono::duration<double>(ttlSeconds);
}

} // anon

// --- AssetLibFetcher ----------------------------------------------------------

AssetLibFetcher::AssetLibFetcher(const std::string& root, const Config& config)
    : _root(root)
    , _config(config)
{
}

std::shared_ptr<AssetLibFetcher>
AssetLibFetcher::ForRoot(const std::string& root)
{
    static std::mutex mutex;
    static std::map<std::string, std::shared_ptr<AssetLibFetcher>> fetchers;

    std::lock_guard<std::mutex> lock(mutex);
    auto it = fetchers.find(root);
    if (it != fetchers.end()) {
        return it->second;
    }
    std::shared_ptr<AssetLibFetcher> fetcher;
    Config config;
    const std::string path = TfStringCatPaths(root, ASSETLIB_CONFIG_FILE);
    if (_ReadConfig(path, &config)) {
        TF_DEBUG(ASSETLIB_RESOLVER).Msg(
            "AssetLibFetcher: library '%s' uses backend: %s\n",
            root.c_str(), _Quote(config.command).c_str());
        fetcher = std::make_shared<AssetLibFetcher>(root, config);
    }
    fetchers[root] = fetcher;
    return fetcher;
}

bool
AssetLibFetcher::IsOffline()
{
    const std::string v = TfGetenv(ASSETLIB_OFFLINE_ENV_VAR);
    return !v.empty() && v != "0";
}

long
AssetLibFetcher::GetRemoteLatest(const std::string& asset)
{
    if (IsOffline()) {
        return -1;
    }
    {
        std::lock_guard<std::mutex> lock(_mutex);
        auto it = _latest.find(asset);
        if (it != _latest.end() &&
            !_Expired(it->second.time, it->second.ttlSeconds)) {
            return it->second.latest;
        }
    }

    std::vector<std::string> argv = _config.command;
    argv.insert(argv.end(), { "list-versions", asset });
    TF_DEBUG(ASSETLIB_RESOLVER).Msg(
        "AssetLibFetcher: %s\n", _Quote(argv).c_str());
    const _HelperResult r = _RunHelper(argv, _config.timeoutSeconds);

    long latest = -1;
    bool ok = r.spawnError.empty() && !r.timedOut && r.exitCode == 0;
    if (ok) {
        const JsValue doc = JsParseString(r.out);
        if (doc.IsObject() && doc.GetJsObject().count("versions") &&
            doc.GetJsObject().at("versions").IsArray()) {
            for (const JsValue& v : doc.GetJsObject().at("versions")
                                        .GetJsArray()) {
                if (v.IsInt()) {
                    latest = std::max(latest, static_cast<long>(v.GetInt()));
                }
            }
        } else {
            ok = false;
        }
    }
    if (!ok) {
        TF_WARN("AssetLibResolver: listing versions of '%s' in '%s' failed "
                "(%s); using local versions only",
                asset.c_str(), _root.c_str(),
                r.exitCode == 0 && r.spawnError.empty() && !r.timedOut
                    ? "invalid JSON output"
                    : _Describe(r, _config.timeoutSeconds).c_str());
    }

    // A failure is cached too (for the failure TTL) so a broken backend
    // isn't asked again for every resolve.
    std::lock_guard<std::mutex> lock(_mutex);
    _latest[asset] = { _Clock::now(), latest,
                       ok ? _config.latestTtlSeconds
                          : _config.failureTtlSeconds };
    return latest;
}

bool
AssetLibFetcher::EnsureVersion(const std::string& asset, long version)
{
    const std::string versionName = AssetLibFormatVersion(version);
    const std::string versionDir =
        TfStringCatPaths(TfStringCatPaths(_root, asset), versionName);
    if (TfIsDir(versionDir)) {
        return true;
    }
    if (IsOffline()) {
        return false;
    }

    const std::string key = asset + "/" + versionName;
    std::promise<bool> promise;
    std::shared_future<bool> future;
    bool owner = false;
    {
        std::lock_guard<std::mutex> lock(_mutex);
        if (auto it = _failures.find(key); it != _failures.end()) {
            if (!_Expired(it->second, _config.failureTtlSeconds)) {
                TF_DEBUG(ASSETLIB_RESOLVER).Msg(
                    "AssetLibFetcher: %s failed recently; not retrying yet\n",
                    key.c_str());
                return false;
            }
            _failures.erase(it);
        }
        if (auto it = _inFlight.find(key); it != _inFlight.end()) {
            future = it->second;
        } else {
            future = promise.get_future().share();
            _inFlight[key] = future;
            owner = true;
        }
    }

    if (!owner) {
        TF_DEBUG(ASSETLIB_RESOLVER).Msg(
            "AssetLibFetcher: waiting for in-flight fetch of %s\n",
            key.c_str());
        return future.get();
    }

    bool ok = false;
    try {
        ok = _Fetch(asset, version, versionDir);
    } catch (...) {
        ok = false;
    }
    {
        std::lock_guard<std::mutex> lock(_mutex);
        _inFlight.erase(key);
        if (!ok) {
            _failures[key] = _Clock::now();
        }
    }
    promise.set_value(ok);
    return ok;
}

bool
AssetLibFetcher::_Fetch(const std::string& asset, long version,
                        const std::string& versionDir)
{
    static std::atomic<unsigned> counter{0};

    const std::string versionName = AssetLibFormatVersion(version);
    const std::string assetDir = TfGetPathName(versionDir);
    const std::string tmpDir = TfStringCatPaths(
        assetDir, TfStringPrintf(".fetch-%s-%d-%u", versionName.c_str(),
                                 static_cast<int>(getpid()), counter++));

    if (!TfMakeDirs(tmpDir, -1, /* existOk = */ false)) {
        TF_WARN("AssetLibResolver: cannot create '%s' to fetch %s %s",
                tmpDir.c_str(), asset.c_str(), versionName.c_str());
        return false;
    }

    std::vector<std::string> argv = _config.command;
    argv.insert(argv.end(),
                { "fetch", asset, std::to_string(version), tmpDir });
    TF_DEBUG(ASSETLIB_RESOLVER).Msg(
        "AssetLibFetcher: %s\n", _Quote(argv).c_str());
    const _HelperResult r = _RunHelper(argv, _config.timeoutSeconds);

    if (!r.spawnError.empty() || r.timedOut || r.exitCode != 0) {
        TfRmTree(tmpDir);
        TF_WARN("AssetLibResolver: fetching %s %s into '%s' failed: %s",
                asset.c_str(), versionName.c_str(), _root.c_str(),
                r.exitCode == 2 && r.spawnError.empty() && !r.timedOut
                    ? ("no such version on the remote: " +
                       TfStringTrim(r.err)).c_str()
                    : _Describe(r, _config.timeoutSeconds).c_str());
        return false;
    }

    // Provenance: where this snapshot came from.
    std::string source;
    const JsValue doc = JsParseString(r.out);
    if (doc.IsObject() && doc.GetJsObject().count("source") &&
        doc.GetJsObject().at("source").IsString()) {
        source = doc.GetJsObject().at("source").GetString();
    }
    JsObject provenance;
    provenance["asset"] = JsValue(asset);
    provenance["version"] = JsValue(versionName);
    provenance["source"] = JsValue(source);
    provenance["fetched_at"] = JsValue(static_cast<int64_t>(time(nullptr)));
    JsArray command;
    for (const std::string& a : _config.command) {
        command.push_back(JsValue(a));
    }
    provenance["backend"] = JsValue(command);
    {
        std::ofstream out(TfStringCatPaths(tmpDir, ASSETLIB_PROVENANCE_FILE));
        JsWriteToStream(JsValue(provenance), out);
    }

    // A fetched version is an exact copy of a revision in the repository:
    // make it read-only so nobody edits the cache by accident (changes go
    // through a new published version instead). Done before the rename, so
    // the version never appears writable.
    std::string whyNot;
    if (!_WriteProtect(tmpDir, &whyNot)) {
        _MakeWritable(tmpDir);
        TfRmTree(tmpDir);
        TF_WARN("AssetLibResolver: cannot write-protect fetched %s %s: %s",
                asset.c_str(), versionName.c_str(), whyNot.c_str());
        return false;
    }

    // Atomic publish into the cache. If another process got there first,
    // keep theirs: versions are immutable, so both are the same snapshot.
    if (rename(tmpDir.c_str(), versionDir.c_str()) != 0) {
        const int err = errno;
        _MakeWritable(tmpDir);
        TfRmTree(tmpDir);
        if (TfIsDir(versionDir)) {
            TF_DEBUG(ASSETLIB_RESOLVER).Msg(
                "AssetLibFetcher: %s %s was fetched concurrently by another "
                "process\n", asset.c_str(), versionName.c_str());
            return true;
        }
        TF_WARN("AssetLibResolver: cannot move fetched %s %s into place: %s",
                asset.c_str(), versionName.c_str(), strerror(err));
        return false;
    }
    TF_DEBUG(ASSETLIB_RESOLVER).Msg(
        "AssetLibFetcher: fetched %s %s (%s)\n",
        asset.c_str(), versionName.c_str(), source.c_str());
    return true;
}

PXR_NAMESPACE_CLOSE_SCOPE
