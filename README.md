# understage-resolver

Part of **Understage**, a backend for USD stages
([understage-pipe](../understage-pipe), [understage-data](../understage-data)).
A C++ OpenUSD **URI resolver** for a versioned asset library. Assets are
grouped into categories and versioned as whole units. Dependencies between
assets are locked by explicit versions, and **release manifests** pin
top-level assets and can be refreshed live on an open stage.

```usda
def "robot" (references = @ustage:robots/franka@) {}
```

It is registered for the `ustage:` scheme *alongside* the default resolver, so
ordinary file paths keep working. The scheme can be changed at build time
(see [Configuration](#configuration)); examples here use the default.

## Library layout

```
<root>/robots/franka/v001/franka.usda          entry layer
<root>/robots/franka/v001/geo.usda             the asset's own files
<root>/robots/franka/v002/...
<root>/components/servos/xm430/v001/xm430.usda
```

- **Folders are the registry.** Categories are ordinary folders. An asset folder contains only `vNNN` version folders.
- **Versions are whole-asset.** A version is a complete, self-contained copy of the asset. The asset's own files are never versioned individually.
- **Entry layer:** each version has exactly one `<asset-name>.{usd,usda,usdc}` file. That is the asset's public interface.

## How it resolves

A URI names an **asset**, never a file inside one. It resolves to the asset's
entry layer, so one asset can depend on another only through that asset's
public interface, never its internals.

| URI | Resolves to |
|---|---|
| `ustage:components/servos/xm430?v=v001` | exactly `v001/xm430.usda` |
| `ustage:robots/franka` | the version pinned in the bound context, else latest |
| `ustage:robots/franka?v=latest` | latest, ignoring pins |

- **Latest** is the highest numbered *published* `vNNN` folder, so `v010` beats `v009`, and a draft is never picked up implicitly (see [Drafts](#drafts-and-publishing)).
- **Library root** comes from the bound context, or else from `$ASSET_LIBRARY_ROOT`.
- **Consistent versions:** resolved paths are plain filesystem paths. Sdf anchors relative paths against the referencing layer's *resolved* path, so `./geo.usda` inside `franka/v001/` always loads `v001/geo.usda`.
- **File format:** `GetExtension` reports the entry layer's real extension, because Sdf picks the file format before resolving.
- **Asset info:** `GetAssetInfo` reports the asset path and the version that was selected.
- **Writing:** only drafts are written. `Sdf.Layer.CreateNew("ustage:components/servos/new")` writes into the asset's open draft, or else creates a new draft after every existing version (`v001` for a new asset). An explicit `?v=` writes into that version only if it is a draft or doesn't exist yet. A published version is never overwritten.
- **Rejected URIs:** a malformed URI, a category instead of an asset, a version-shaped path segment, `..`, or two entry layers in one version all resolve to nothing.

## Published-asset rule

Inside a published asset version, the rules for paths are:

| Reference | Allowed |
|---|---|
| `./geo.usda`, `meshes/link1.usdc` | yes. These are the asset's own files, which are never versioned. |
| `ustage:components/servos/xm430?v=v002` | yes. Another asset, with an explicit version. |
| `ustage:components/servos/xm430` | **no**: unversioned |
| `ustage:components/servos/xm430?v=latest` | **no**: floating |

So each asset version records, in its own references, exactly which versions
of its dependencies it was built against, and no lockfile is needed. Publishing
a new servo cannot change an existing robot. Picking up the new servo means
publishing a new robot version.

The resolver enforces this. A forbidden reference made from any layer under
`<root>/.../vNNN/` is refused with a warning and rewritten to
`ustage:<asset>?error=unversioned-ref-in-published-asset`, which can never
resolve. It then shows up as an ordinary composition error
(`Pcp.ErrorInvalidAssetPath`) instead of silently floating to latest. The
identifier is deliberately not empty: an empty reference path means an
*internal* reference to the layer's own `defaultPrim`.

Scenes and work files that live outside the library may use unversioned
references. They resolve through manifest pins and then latest.

## Drafts and publishing

A version starts life as a **draft**: a `vNNN/` folder holding a
`.assetlib-draft.json` marker. Any version without the marker is published.

| | Draft | Published |
|---|---|---|
| Marker | `.assetlib-draft.json` | none; `.assetlib-source.json` records where it was published or fetched from |
| `ustage:asset`, `?v=latest` | skipped | the newest one is selected |
| `?v=vNNN`, manifest pins | resolve | resolve |
| Writes (`Sdf.Layer.CreateNew`) | allowed | refused |
| Files | writable | write-protected, once published by a tool that does it (`ustage publish`, fetches) |

- **The open draft.** If an asset's newest version is a draft, unversioned writes go into it, so repeated saves while working on an asset land in one draft rather than a new version each time. Otherwise the first write creates the next version as a draft.
- **Resolving for a new asset creates the draft.** `ResolveForNewAsset` makes the version folder and its marker together (built under a temporary name, then renamed), so the answer is stable across calls and no reader ever sees an empty folder that looks published. An empty draft is harmless: nothing resolves it implicitly.
- **The published-asset rule applies to drafts too.** A draft must already reference other assets by explicit version, and it must not reference another draft, because that can still change.
- **Publishing** is done by the pipeline (`ustage publish` in [understage-pipe](../understage-pipe)): it checks the draft, submits it through the library's backend if there is one (the backend's `publish` command, below), records `.assetlib-source.json`, removes the marker and write-protects the folder.
- **Existing libraries keep working.** Folders written before drafts existed have no marker, so they are published.

## Release manifests

A manifest pins top-level assets by their category path:

```json
{ "root": "../library", "assets": { "robots/franka": "v001" } }
```

```python
from pxr import Ar, Usd
import AssetLibResolver

ctx = AssetLibResolver.ResolverContext("release.json")
stage = Usd.Stage.Open("scene.usda", Ar.ResolverContext(ctx))

# ...edit release.json to bump robots/franka to v002...
Ar.GetResolver().RefreshContext(Ar.ResolverContext(ctx))   # the stage recomposes
```

When franka moves to `v002`, its servo moves to whatever `v002` references,
because dependency versions travel with the asset. A manifest pin on the
servo itself affects only unversioned references, which can appear only in
scenes.

A manifest-backed context's identity is its manifest path. Every context
created for that path shares the same state. `RefreshContext` re-reads the file
and sends `ArNotice::ResolverChanged` only to stages bound to that context.

Other ways to get a context:
- `ResolverContext.FromPins({...}, libraryRoot)`: inline and immutable.
- `Ar.GetResolver().CreateContextFromString("ustage", "release.json")`.
- Set `$ASSET_LIBRARY_MANIFEST` to make that manifest the default context. It
  then also applies with nothing bound, for example in `usdview ustage:robots/franka`.

Set `TF_DEBUG=ASSETLIB_RESOLVER` to trace every identifier and resolve call.

## Remote libraries (Perforce and others)

Published assets can live in a version-control system instead of on local
disk. The repository needs **no version folders**: in Perforce the library is
just `//depot/assets/robots/franka/franka.usda` and so on. The local library
root then becomes a **cache of immutable snapshots**:

```
<root>/.assetlib.json                       backend configuration
<root>/robots/franka/v002/                  fetched snapshot, read-only
<root>/robots/franka/v002/.assetlib-source.json   where it came from (…@12345)
```

When the resolver needs a version that isn't cached, it **fetches it before
returning**.
- **A version is a snapshot, not a sync.** Different robots may pin different servo versions, so several versions of one asset must exist side by side. A workspace sync can only hold one revision per path, so each version is exported (`p4 print` at a changelist) into its own `vNNN/` folder.
- **Perforce version numbers:** asset version N is the **Nth submitted changelist that touched the asset's depot path**. So `v=14` means "the 14th change to this asset", whatever the global changelist number is.
- **Blocking fetch, made cheap:**
  - `ArResolver::Resolve` is synchronous, and an async fetch would hand headless tools (simulation, CI) a silently incomplete stage. So a cache miss blocks.
  - One fetch per asset version, however many composition threads ask.
  - Failures back off instead of being retried on every resolve.
  - Fetched versions are never refetched.
- **Prefetch:** fill the cache in parallel *before* opening a stage. Every reference inside a published asset carries an explicit version, so a scene's whole dependency graph is known up front:

  ```bash
  python -m AssetLibResolver.prefetch scene.usda --manifest release.json -j 8
  ustage prefetch scene.usda --manifest release.json     # the same, via understage-pipe
  ```

  From Python, use `AssetLibResolver.prefetch.prefetch([...])`, or `AssetLibResolver.Prefetch(uris)` for a flat list.
- **Write-protected cache:** every fetched file and directory loses its write permission before it becomes visible, so a cached copy of a repository revision can't be edited, deleted or overwritten by accident. Sdf saves fail, and the resolver refuses to create new layers inside a fetched version. Changes go into a new version: omit `?v=` to target the next one.

  To delete a cached version on purpose, run `chmod -R u+w` on it first. It will be fetched again when it's next needed.
- **Atomic and crash-safe:**
  - Snapshots are written to a temporary folder and renamed into place.
  - A half-fetched or failed version is never visible.
  - Two processes fetching the same version at once is harmless.
- **Offline mode:** `ASSET_LIBRARY_OFFLINE=1` uses only what is already cached.
- **Unversioned paths:** `latest` and new-version numbering both take the remote's versions into account.

Backends are external commands speaking a small JSON protocol
([docs/backend-protocol.md](docs/backend-protocol.md)), configured per library.
Besides listing and fetching versions, a backend can **publish** one: submit a
draft as the asset's next revision, refusing if the remote's next version
number isn't the draft's.

```json
{ "backend": { "command": ["python3", "-m", "assetlib_backends.p4",
                           "--port", "ssl:perforce:1666", "--depot-root", "//depot/assets"] } }
```

Included backends:

| Backend | Version N is | Exported with | Published with |
|---|---|---|---|
| `assetlib_backends.p4` (Perforce, `p4` CLI, no P4Python) | the Nth submitted changelist touching `<depot-root>/<asset>/...` | `p4 print` at that changelist | one changelist, from a throwaway client that maps only the asset (`sync -k`, `reconcile`, `submit`) |
| `assetlib_backends.lore` ([Lore](https://github.com/EpicGames/lore), `lore` CLI) | the Nth revision changing `<asset>/` (`lore file history`) | `lore repository dump` + `lore file write --address`, in a private **bare** instance (`lore clone --bare`: metadata only, no working tree) | `stage --scan`, `commit`, `push` from a temporary working-tree clone (a full checkout) |
| `assetlib_backends.dirremote` | `<remote>/<asset>/<N>/` | a copy. Used by the tests, and the smallest example to copy for a new backend | a copy, renamed into place |

```json
{ "backend": { "command": ["python3", "-m", "assetlib_backends.lore",
                           "--url", "lore://lore.example.com:41337/assets"] } }
```

The Lore backend was tested with Lore v0.10.0. Lore is pre-1.0 and its CLI has
no JSON output, so the backend parses the text output strictly and fails
loudly if the format changes. Its options:
- `--root`: the library lives in a subdirectory of the repository.
- `--branch`: which branch's history defines the versions.
- `--instance`: where the bare instance lives. The default is `$XDG_CACHE_HOME/assetlib/lore/`.

Fetching uses POSIX process APIs (`posix_spawn`, process groups), so it's
Linux/macOS only for now.

**Live resolvers** read from a VCS while a stage composes, with no snapshots
on disk. They're kept in [`live/`](live/), separate from this cache workflow,
which remains the default because many tools can only open real files. A live
Lore resolver is designed there but not built yet.

## Tools without resolver support: localize

Some software reads USD but can't load an `ArResolver` plugin. For those, `localize` bakes a scene into plain files. It resolves every `ustage:` URI once, under a manifest or the default context, and writes a self-contained copy that uses only relative paths:

```bash
python -m AssetLibResolver.localize scene.usda -o out/ --manifest release.json
ustage localize scene.usda -o out/ --manifest release.json     # the same, via understage-pipe
```

```
out/scene.usda                      top layer: open this one
out/scene/...                       the scene's own layers and files, mirrored
out/lib/<asset>/vNNN/...            each asset version used, copied whole
out/external/...                    files from outside the scene folder
```

- **Rewritten layers.** Every layer is rewritten through `UsdUtils.ModifyAssetPaths`, which covers sublayers, references, payloads and asset-valued attributes. Each `ustage:` URI becomes a relative path to the copied entry layer. Relative paths inside an asset are unchanged, because the version folder is copied whole. Crate layers stay crate.
- **Top layer.** The top layer sublayers the scene and copies its stage metadata (defaultPrim, upAxis, units, time range). It also records the source and manifest (as paths relative to the output folder) and every asset version baked in, under `customLayerData["assetlib:localized"]`.
- **A snapshot, not a link.** Unversioned scene references are frozen at whatever they resolved to, so run localize again for a new release. Missing versions are prefetched first.
- **Plain, writable copies.** Copies are ordinary writable files, even when the cache is write-protected. No backend config is copied.
- **Safe re-runs.** `--force` replaces an earlier output. It refuses to delete a folder that localize didn't create.
- **Failures.** A path that can't be resolved is left as authored and reported, and the command exits non-zero.

## Houdini

[`houdini/`](houdini/) builds the same resolver against the USD inside a
Houdini install (`$HFS`) as a Houdini package. It works in Solaris as a USD
plugin, and in every other Houdini file parameter through an HDK `FS_` file
helper. See [houdini/README.md](houdini/README.md).

## Configuration

| Setting | How | Default |
|---|---|---|
| Library root | environment variable, or `root` in a release manifest (the manifest wins) | `$ASSET_LIBRARY_ROOT` |
| Default release manifest | environment variable | `$ASSET_LIBRARY_MANIFEST` |
| URI scheme | CMake `-DASSETLIB_URI_SCHEME=<scheme>` | `ustage` |
| Offline mode | environment variable | `$ASSET_LIBRARY_OFFLINE` |
| Root / manifest / offline variable names | CMake `-DASSETLIB_ROOT_ENV_VAR=...`, `-DASSETLIB_MANIFEST_ENV_VAR=...`, `-DASSETLIB_OFFLINE_ENV_VAR=...` | as above |
| Library config / provenance / draft marker file names | CMake `-DASSETLIB_CONFIG_FILE=...`, `-DASSETLIB_PROVENANCE_FILE=...`, `-DASSETLIB_DRAFT_FILE=...` | `.assetlib.json`, `.assetlib-source.json`, `.assetlib-draft.json` |

The build-time settings are written into a generated `config.h` (from
`src/AssetLibResolver/config.h.in`) and into `plugInfo.json`, so the C++ code and
the plugin registration can't disagree. The scheme must be a lowercase URI
scheme, given without the colon; CMake rejects anything else. Python can query
a build's settings:

```python
AssetLibResolver.GetUriScheme()       # 'lib'
AssetLibResolver.GetRootEnvVar()      # 'ASSET_LIBRARY_ROOT'
AssetLibResolver.GetManifestEnvVar()  # 'ASSET_LIBRARY_MANIFEST'
```

The tests read these values too, so the suite passes for any configuration.

## Build

Requires an OpenUSD install with Python (tested with v25.11 built via
`build_usd.py`).

```bash
export USD_INSTALL_ROOT=/path/to/usd/25.11   # your OpenUSD install
source env.sh
cmake -S . -B build -G Ninja -DCMAKE_PREFIX_PATH=$USD_INSTALL_ROOT \
      -DCMAKE_INSTALL_PREFIX=$PWD/install          # [-DASSETLIB_URI_SCHEME=...]
cmake --build build && cmake --install build
uvx --python 3.12 pytest tests    # Python must match the one USD was built against
```

The Perforce tests start a throwaway `p4d` per test through an `rsh:` port, so
there's no server to manage. They are skipped unless `p4` and `p4d` are on
`PATH` or in `$P4_BIN_DIR`. The binaries are a free download from Perforce;
verify their GPG signatures.

The Lore tests start one `loreserver` per session. It runs on free local
ports with its own store and client directories, so it never touches a real
Lore setup. They are skipped unless `lore` and `loreserver` are on `PATH` or
in `$LORE_BIN_DIR`. Install them from the
[GitHub releases](https://github.com/EpicGames/lore/releases) and check each
tarball's SHA256 against the digest GitHub lists for the asset. The official
`install.sh` doesn't verify downloads, and it edits your shell rc file.

Install layout (mirrors OpenUSD's own):

```
install/plugin/usd/plugInfo.json                       Includes */resources/
install/plugin/usd/AssetLibResolver.so
install/plugin/usd/AssetLibResolver/resources/plugInfo.json
install/lib/python/AssetLibResolver/                   Python bindings, prefetch, localize
install/lib/python/assetlib_backends/                  backend helpers
```

## Code tour

- `src/AssetLibResolver/resolver.{h,cpp}`: the `ArResolver` subclass. It handles URI parsing, version and entry-layer selection, the published-asset rule and the fallback default context.
- `src/AssetLibResolver/resolverContext.{h,cpp}`: the manifest/pin context, with shared state and identity semantics.
- `src/AssetLibResolver/wrapResolverContext.cpp`: Python bindings (`pxr_boost::python`).
- `src/AssetLibResolver/fetcher.{h,cpp}`: fetching from a backend. It runs the helper process, deduplicates in-flight requests, materializes snapshots atomically, write-protects them, and caches failures and version lists.
- `src/AssetLibResolver/prefetch.py`: the dependency-graph walk and the prefetch CLI.
- `src/AssetLibResolver/localize.py`: bakes a scene into resolver-free files.
- `cmake/AssetLibConfig.cmake`: the build settings, generated `config.h` and source lists, shared by every build.
- `houdini/`: the Houdini package build, the FS helper (`src/FS_AssetLibHelper.C`) and the Houdini test runner.
- `python/assetlib_backends/`: the backend helpers (Perforce, Lore, directory) and the shared protocol code.
- `tests/test_resolver.py`: covers resolution, pins, anchoring, dependency locking, the published-asset rule, live refresh, writing and drafts.
- `tests/test_fetch.py`: covers fetch on miss, latest, offline mode, concurrency, failures, timeouts, write protection, prefetch, drafts against a remote and publishing through the directory backend.
- `tests/test_localize.py`: localized output opened by a USD with no resolver plugin.
- `tests/test_p4.py`: the Perforce backend, fetching and publishing, against a real `p4d`.
- `tests/test_lore.py`: the Lore backend, fetching and publishing, against a real `loreserver`.

## License

Apache-2.0
