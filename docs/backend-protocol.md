# Backend helper protocol (v1)

A **backend** connects an asset library to a remote repository (Perforce,
Lore, git, an HTTP store, ...). It is an external command that the resolver
runs. The resolver links no VCS code and runs no Python inside USD's
composition threads.

## Configuration

`<library root>/.assetlib.json`:

```json
{
  "backend": {
    "command": ["python3", "-m", "assetlib_backends.p4",
                "--port", "ssl:perforce:1666", "--depot-root", "//depot/assets"],
    "timeout_s": 600,
    "latest_ttl_s": 60,
    "failure_ttl_s": 30
  }
}
```

| Key | Meaning | Default |
|---|---|---|
| `command` | argv prefix; the resolver appends a subcommand (below). Looked up on `PATH`. The environment is inherited. | required |
| `timeout_s` | kill the helper (and its process group) after this long | 600 |
| `latest_ttl_s` | how long a `list-versions` answer is reused | 60 |
| `failure_ttl_s` | how long a failed fetch or listing is remembered before retrying | 30 |

A library without this file, or without `backend`, is local-only. Setting
`$ASSET_LIBRARY_OFFLINE=1` disables every backend call.

## Commands

The helper reads nothing from stdin. On success it writes **one JSON object**
to stdout. Diagnostics go to stderr, which the resolver includes in its
warning.

### `list-versions <asset>`

```json
{"versions": [1, 2, 3], "protocol": 1}
```

All versions the remote has for `<asset>` (e.g. `robots/franka`), as
integers starting at 1. An unknown asset returns `[]` and exit code 0. Only
the maximum value is used today; it is how `latest` sees versions that
haven't been fetched yet.

### `fetch <asset> <N> <dest>`

Write the complete asset, as of version `N`, into the existing, empty
directory `<dest>`. Lay the files out relative to the asset root, so the
entry layer is `<dest>/<leaf>.usd[a|c]`. Then print:

```json
{"source": "//depot/assets/robots/franka/...@12345", "protocol": 1}
```

`source` is free-form provenance. It is stored in the fetched version's
`.assetlib-source.json`.

The helper must not write outside `<dest>`. The resolver owns everything
else:
- `<dest>` is a temporary sibling of the final `vNNN` directory.
- After a successful exit, the resolver write-protects it and renames it into place atomically.
- After a failed exit, it deletes it.

So a helper never needs to clean up after itself and never sees, or
creates, `vNNN` names.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success; stdout holds the JSON object |
| 2 | the asset or version does not exist on the remote |
| other | any other failure |

## Guarantees the resolver relies on

- **Versions are immutable and append-only.** Version N of an asset always
  has the same content. A fetched version is never refetched, and nothing
  invalidates it.
- **Versions are whole-asset snapshots.** One fetch yields every file the
  asset's layers reference by relative path.

## Writing a backend in Python

`assetlib_backends._protocol.main` handles argument parsing, the JSON
output, exit codes and path-safety checks. A backend implements two methods:

```python
from assetlib_backends._protocol import NotFound, main

class MyBackend:
    def list_versions(self, asset) -> list[int]: ...
    def fetch(self, asset, version, dest) -> str: ...   # returns provenance

if __name__ == "__main__":
    raise SystemExit(main(lambda args: MyBackend()))
```

See these examples:
- `assetlib_backends/dirremote.py`: the minimal backend.
- `assetlib_backends/p4.py`: Perforce.
- `assetlib_backends/lore.py`: Lore.
