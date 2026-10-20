# Houdini package

Builds the asset library resolver **against the USD that ships with Houdini**
(`$HFS`) and packages it for Houdini. It works in both places:
- **Solaris / LOPs:** a USD `ArResolver` plugin, so `ustage:` references,
  sublayers and payloads resolve in any stage.
- **Everywhere else:** an HDK `FS_` file helper, so any Houdini file parameter
  (File SOP, USD Import, texture paths, `hou.readFile`, ...) accepts
  `ustage:robots/franka?v=2`.

Both go through the same resolver instance in the Houdini process, so they
share:
- version selection,
- remote fetching from Perforce or Lore,
- the write-protected cache.

Houdini's USD is generally a different version from the one Isaac Sim or a
standalone OpenUSD build uses. All builds come from the same sources but are
separate binaries, and **a Houdini package only loads in the Houdini version
it was built for**. The package file enforces that with
`houdini_version == 'X.Y'`.

| Houdini | USD | Python | C++ | Boost.Python | Notes |
|---|---|---|---|---|---|
| 20.5 | 0.24.03 | 3.11 | 17 | SideFX `hboost` | latest supported by the NVIDIA Omniverse Houdini bridge |
| 22.0 | 0.26.05 | 3.13 | 20 | USD's own `pxr_boost` | |

`src/AssetLibResolver/pyCompat.h` chooses the right Boost.Python, and the C++
standard comes from Houdini's own build flags. Nothing is version-specific in
the sources.

## Build

Use one build directory per Houdini version:

```bash
for v in 20.5 22.0; do
    HFS=/opt/hfs$v cmake -S houdini -B houdini/build/houdini$v -G Ninja \
        -DCMAKE_BUILD_TYPE=Release
    cmake --build houdini/build/houdini$v && cmake --install houdini/build/houdini$v
done
```

`HFS` can also be passed as `-DHFS=...`. Each package is written to
`houdini/install/houdini<X.Y>/`; set `CMAKE_INSTALL_PREFIX` to put it
somewhere else.

The URI scheme and the variable names come from the same CMake settings as
the standalone build (`-DASSETLIB_URI_SCHEME=...` and the others; see the
main README). You can also bake library defaults into the package file:

```bash
cmake ... -DASSETLIB_HOUDINI_LIBRARY_ROOT=/studio/assetlib \
          -DASSETLIB_HOUDINI_MANIFEST=/studio/releases/current.json
```

## Enable in Houdini

Copy, or symlink, the generated package file into a Houdini packages folder:

```bash
for v in 20.5 22.0; do
    mkdir -p ~/houdini$v/packages
    ln -s "$PWD/houdini/install/houdini$v/AssetLibResolver.json" ~/houdini$v/packages/
done
```

The package:
- adds itself to `HOUDINI_PATH`, so Houdini finds `dso/usd_plugins`,
  `dso/fs` and `python3.<N>libs`;
- puts the backend helpers on `PYTHONPATH`;
- optionally sets the library root and manifest.

Check it in a Houdini Python shell:

```python
from pxr import Ar
Ar.GetResolver().Resolve("ustage:robots/franka")       # Solaris side
hou.readFile("ustage:robots/franka")[:40]              # FS helper side
import AssetLibResolver; AssetLibResolver.GetUriScheme()
```

## Troubleshooting

When `ustage:` paths don't resolve in a session, run the diagnostics from
Houdini's Python Shell. It's a single line, so it pastes cleanly:

```python
TEST_URI = "ustage:robots/arm2dof?v=1"; exec(open("/path/to/understage-resolver/houdini/tools/diagnose.py").read())
```

It reports:
- the package and environment;
- whether the USD plugin is registered and loaded;
- the other resolvers present;
- whether `ustage:` is handled;
- for `TEST_URI`, **why** it didn't resolve: which library root was searched,
  where that root came from, and which versions exist.

The most common cause is simply the wrong library. Houdini launched from the
desktop or the launcher gets only the package's `ASSET_LIBRARY_ROOT` and no
manifest. The root is read on every lookup, so you can switch it in a running
session without restarting:

```python
import os; os.environ["ASSET_LIBRARY_ROOT"] = "/path/to/library"
```

A manifest (`ASSET_LIBRARY_MANIFEST`) is read once, when the session starts.

For a full trace, launch Houdini with `TF_DEBUG=ASSETLIB_RESOLVER` for
resolution and fetching, or `TF_DEBUG="PLUG_*"` if the plugin doesn't load.

## Package layout

```
AssetLibResolver.json                               Houdini package file
dso/usd/AssetLibResolver.so                         USD resolver plugin
dso/usd_plugins/plugInfo.json                       (Includes */resources/)
dso/usd_plugins/AssetLibResolver/resources/plugInfo.json
dso/fs/FS_AssetLibHelper.so                         FS helper (non-Solaris)
python3.<N>libs/AssetLibResolver/                   Python bindings + prefetch
backends/assetlib_backends/                         Perforce / Lore / dir helpers
```

## How the file helper behaves

- **Reading:** a `ustage:` path is resolved through USD's `ArGetResolver()` to a
  local file, which is fetched first if needed, and then read. No stage is
  involved, so the resolver uses its default context: `$ASSET_LIBRARY_MANIFEST`
  if it's set, and the root from `$ASSET_LIBRARY_ROOT`.
- **`?v=` handling:** Houdini normally reads `?` as an index-file section
  separator. The helper claims its own paths so that `?v=2` stays a version.
  Index-file sections inside library assets aren't supported.
- **Extension:** the reported extension is the entry layer's (`.usd`/`.usda`/`.usdc`),
  so Houdini picks the right file format even though the URI has none.
- **Read-only:** writes to `ustage:` paths fail, and Houdini never creates a
  local file named `ustage:...`. Publishing goes through the pipeline, not
  through a file parameter.
- **Plugin registration:** Houdini registers the USD plugin itself from
  `dso/usd_plugins`. As a safety net for processes where it hasn't, the
  helper registers it on the first `ustage:` path it sees, **never at startup**.
  Calling into USD's plugin registry from `installFSHelpers()` forces USD plugin
  discovery during Houdini's startup, and leaves the interactive Houdini 22.0
  viewport drawing nothing, even for scenes that don't use `ustage:` at all.
  `hython` and batch rendering are not affected.

## Tests

```bash
HFS=/opt/hfs20.5 houdini/run_tests.sh -q
HFS=/opt/hfs22.0 houdini/run_tests.sh -q
```

This runs three layers of tests:
1. **The whole resolver suite,** with Houdini's own Python and USD against
   the built package, including the Perforce and Lore backends.
2. **`tests/test_houdini_fs.py`:** drives the FS helper through Houdini's
   file layer using `fs_helper_probe`, a standalone HDK program built alongside
   the package that loads the helpers the way Houdini does.
3. **`tests/test_hython.py`:** runs `houdini/tests/hython_checks.py` inside
   real Houdini (`hython`), with the package enabled through
   `HOUDINI_PACKAGE_DIR`. Your Houdini preferences are left alone. It checks:
   - Solaris Sublayer LOPs on `ustage:` assets, including versioned
     dependencies, `latest`, Python context pins, and a remote fetch during a
     LOP cook;
   - outside Solaris, `hou.readFile` and a File SOP loading a `ustage:` USD
     asset as geometry;
   - that writing geometry to a `ustage:` path fails and leaves no local file.

Layers 1 and 2 need no Houdini license, because they use only `pxr` and the
HDK's `UT`/`FS` libraries. Layer 3 needs one, and is skipped if `hython` can't
get one.

Tested with Houdini 20.5.684 (USD 0.24.03, Python 3.11), and Houdini 22.0.429
and 22.0.466 (USD 0.26.05, Python 3.13): 76 tests each, including the
in-Houdini checks. Builds for the same `X.Y` share one package folder and
one package file, so build against the patch release you actually run. The standalone build covers
USD 25.11. The tests use `Usd.Stage.GetCompositionErrors()` where it exists
and fall back to prim-index errors on older USD.

## Coexisting with other resolvers (Omniverse)

This resolver is a URI resolver: it handles only `ustage:` paths. Houdini's
primary resolver, or another studio or vendor resolver such as the NVIDIA
Omniverse connector's `OmniUsdResolver`, keeps handling everything else.

On the development machine, the Omniverse Houdini connector
`v200.1.0` for Houdini 20.5 failed to load in Houdini 20.5.684, with or
without this package:

```
Failed to load plugin 'Omniverse USD Plugin': libomni_usd_resolver.so:
undefined symbol: ...ArResolver17_IsRepositoryPathERKSs
```

The `Ss` in that symbol is the pre-C++11 `std::string` ABI, while this
Houdini's USD uses the C++11 ABI, so that connector build doesn't match this
Houdini build. Houdini then falls back to its default resolver. Use a
connector build that matches the exact Houdini 20.5 build.

