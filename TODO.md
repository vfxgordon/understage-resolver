# TODO

## Deferred

- [ ] **Live Lore resolver** (`lore:` scheme, reads straight from Lore with no
  local snapshot). The design and open questions are in
  [live/lore/README.md](live/lore/README.md). The open questions are:
  - threading: Lore's callback-based API called from Pcp's threads
  - `liblore` version pinning
  - linking `liblore` into the USD process: Rust runtime/allocator vs TBB
  - authentication tokens

  Use the `assetlib_backends.lore` cache backend in the meantime.
