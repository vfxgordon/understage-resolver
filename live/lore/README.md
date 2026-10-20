# Live Lore resolver (design)

Status: **not implemented**. These notes cover the design and the Lore APIs it
would use. They come from the Lore v0.10.0 docs and C header
(`lore-capi/lore.h`). To use Lore today, use the cache backend
`assetlib_backends.lore`.

## Idea

Read asset files directly from a Lore repository through its C API
(`liblore`), at a given revision, with no working tree and no `vNNN/`
snapshot. Lore's local store is already a content-addressed cache of
fragments, with size-limited eviction, shared by every instance on the
machine. So caching, deduplication across versions, and immutability come
from Lore itself.

## Identifiers and resolved paths

```
lore:robots/franka?v=3                                     identifier (asset, version)
lore://<host:port>/<repo>/<revision-hash>/robots/franka/franka.usda   resolved path
```

- **The revision hash is in the resolved path.** Sdf anchors a relative path
  like `./geo.usda` against the referencing layer's resolved path, so it
  stays at the same revision. That gives the same version consistency that
  `vNNN/` folders give the cache resolver.
- **The resolver handles both URI forms.** Ar sends `CreateIdentifier` to
  whichever resolver owns the *anchor's* scheme, so the anchoring above only
  works if this resolver is registered for its resolved-path form too.
- **Version N** uses the same rule as the cache backend: the Nth revision
  that changed the asset's directory. An alternative is a publish step that
  tags each revision with metadata (`asset.<path>.version = N`); it is then
  found with `lore_revision_find` by key and value.

## C API calls

| Step | Call |
|---|---|
| open the remote store | `lore_storage_open` |
| revision number or metadata -> hash | `lore_revision_find` |
| history of the asset path (version N) | `lore_file_history` |
| load a revision's tree in memory (no workspace) | `lore_revision_tree_load(store, repository, revision_hash)` |
| path -> node | `lore_revision_tree_resolve_path` |
| node -> size and content address | `lore_revision_tree_node_info` |
| bytes, with offset and length | `lore_storage_get` |

The `ArAsset` subclass would read on demand:
- `GetSize()` returns the node size.
- `Read(buffer, count, offset)` calls `lore_storage_get` with that offset and length.
- `GetBuffer()` reads the whole asset once.
- `GetFileUnsafe()` returns null.

Sdf's `.usda` and `.usdc` readers work from `ArAsset` reads, the same way
`.usdz` packages are read.

## Open questions

- **Threading.** The API delivers results through callbacks and runs on
  Lore's own thread pool, while Ar resolves from Pcp's composition threads.
  Wrap each call as a blocking call per request, and check that it doesn't
  deadlock under TBB.
- **Stability.** Lore is pre-1.0, and its interfaces and formats may change
  between releases. Pin a `liblore` version and gate the build on it.
- **Linking.** Linking `liblore` (a Rust runtime and allocator) into the USD
  process needs checking against USD's own allocator and its TBB usage.
- **Authentication.** Real servers need tokens, so plan for
  `lore_auth_login_with_token` and handling a token that expires mid-session.
- **Non-USD files.** Textures and meshes that USD or Hydra open through `Ar`
  work. Anything opened by path does not, which is why the cache workflow
  remains the default.
