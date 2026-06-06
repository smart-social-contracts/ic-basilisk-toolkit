# IC File Registry

General-purpose on-chain file storage for the Internet Computer.

Stores arbitrary files (extensions, WASMs, codex scripts, frontend assets) indexed by `(namespace, path)` with:
- **HTTP serving with CORS** — browser `fetch()` / `<script src>` directly from the canister URL
- **Inter-canister Candid API** — realm backends fetch Python/WASM via `get_file` without HTTP outcalls
- **Per-namespace publisher ACL** — grant upload access per namespace without exposing full controller access
- **Chunked upload** — files > 500 KB are automatically split and reassembled (handles WASMs up to ~400 MB)
- **Internet Identity frontend** — browse, upload, manage via browser with II login

Built with [basilisk](https://github.com/smart-social-contracts/basilisk) — Python on the IC.

## Architecture

```
Browser / CLI
    │
    ├── HTTPS  →  GET /{namespace}/{path}    (CORS, public)
    └── Candid →  store_file / get_file / ...

Realm backend (inter-canister)
    └── Candid →  get_file("extensions/hello_world", "entry.py")
```

Files are stored in the canister's persistent filesystem (`/registry/...`), backed by `StableBTreeMap` in stable memory — survives upgrades automatically.

## API

### Public queries (no auth)
| Method | Args | Description |
|---|---|---|
| `list_namespaces()` | — | List all namespaces with file counts and sizes |
| `list_files(args)` | `{namespace}` | List files in a namespace |
| `get_file(args)` | `{namespace, path}` | Get file content (base64) + metadata |
| `get_stats()` | — | Overall storage statistics |
| `get_acl()` | — | Publisher ACL for all namespaces |
| `http_request(req)` | HTTP | Serve files via HTTP with CORS |

### Authenticated updates (publisher or controller)
| Method | Args | Description |
|---|---|---|
| `store_file(args)` | `{namespace, path, content_b64, content_type?}` | Upload file (≤ 500 KB) |
| `store_file_chunk(args)` | `{namespace, path, chunk_index, total_chunks, data_b64}` | Upload one chunk |
| `finalize_chunked_file(args)` | `{namespace, path}` | Assemble chunks into file |
| `delete_file(args)` | `{namespace, path}` | Delete a file |

### Controller-only
| Method | Args | Description |
|---|---|---|
| `grant_publish(args)` | `{namespace, principal}` | Grant upload access |
| `revoke_publish(args)` | `{namespace, principal}` | Revoke upload access |
| `delete_namespace(args)` | `{namespace}` | Delete namespace + all files |
| `update_namespace(args)` | `{namespace, description}` | Update namespace metadata |

## HTTP serving

Files are served at:
```
https://{canister_id}.icp0.io/{namespace}/{path}
```

With CORS headers — suitable for:
```html
<script type="module" src="https://{canister}.icp0.io/extensions/hello_world/main.js"></script>
```
```python
# Inter-canister (realm backend)
result = yield registry_canister.get_file('{"namespace": "extensions/hello_world", "path": "entry.py"}')
```

## CLI upload

```bash
# Single file
python3 scripts/upload_file.py \
  --canister <CANISTER_ID> \
  --namespace extensions/hello_world \
  --file ./hello_world/backend/entry.py \
  --network ic

# Entire directory
python3 scripts/upload_file.py \
  --canister <CANISTER_ID> \
  --namespace extensions/hello_world \
  --dir ./hello_world/ \
  --network ic
```

## Deploy

Deployed with [`icp-cli`](https://github.com/dfinity/icp-cli) (`icp.yaml`); **dfx is not used**.

```bash
# Install deps
pip install ic-basilisk-toolkit

# Build backend WASM + deploy (local)
make deploy

# Mainnet
make deploy-ic
```

Under the hood `make deploy` builds the backend WASM and the SvelteKit frontend,
then runs `icp deploy` (`make deploy-ic` uses `icp deploy -e ic`).

`make deploy-ic` injects the mainnet backend canister id into the frontend build.
For a custom backend, set `VITE_CANISTER_ID` yourself:
```bash
VITE_CANISTER_ID=<your-canister-id> npm --prefix frontend run build
```

## File size limits

| Limit | Value |
|---|---|
| Per-file max (single call) | 500 KB |
| Per-file max (chunked) | ~400 MB (practical) |
| Per-file hard limit (stable memory) | 2 MB per chunk |
| Total storage | 50 MB default (soft limit, adjustable) |

Files > 500 KB are automatically uploaded in chunks by `scripts/upload_file.py` and the frontend.
