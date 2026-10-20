# Live resolvers

A **live resolver** reads published assets straight from a version-control
system while USD composes the stage. There's no local snapshot on disk: the
resolver returns its own URIs and opens them through a custom `ArAsset`.

Live resolvers are kept separate from the cache workflow in the rest of this
repository on purpose.

| | Cache workflow (`ustage:` + `python/assetlib_backends/`) | Live resolver (here) |
|---|---|---|
| Where files live | read-only `vNNN/` snapshots in the local library | only in the VCS, plus its own content cache |
| Who can read them | anything: USD, mesh loaders, simulators, MuJoCo/URDF tools, scripts | only code that reads through USD's `Ar` |
| Status | built and tested (Perforce, Lore) | design stage |

The cache workflow stays the default, because a lot of software in a
physical-AI pipeline opens files by path and will never go through USD's
resolver. A live resolver is an optional extra for USD-only consumers:
- it avoids writing snapshots to disk;
- it shares unchanged content between versions through the VCS's own store.

Each live resolver will be:
- its own plugin and CMake target, built only when explicitly enabled, e.g. `-DASSETLIB_BUILD_LIVE_LORE=ON`;
- registered for its own URI scheme, e.g. `lore:`;
- independent of the `ustage:` cache resolver, so either can be installed without the other.

| Directory | VCS |
|---|---|
| [`lore/`](lore/) | Epic Games Lore |
