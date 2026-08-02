# Todo List — Basilisk Template

Multi-user todo lists on a Basilisk canister. Users interact via a **sandboxed REPL**
(`__shell__` / `basilisk-toolkit shell`); mutations are authorized by **Cedar**
owner-only policies on the host side. Entity CRUD, Cedar schema, RPC handlers, and
sandbox wiring are provided by `SecureEntity` — one `setup_secure_orm()` call
replaces the hand-written boilerplate from the `my_project` prototype.

## What it demonstrates


| Feature                        | How                                                                    |
| ------------------------------ | ---------------------------------------------------------------------- |
| **Sandboxed REPL**             | Basilisk subinterpreter; user code never touches the DB directly       |
| **Cedar owner-only auth**      | Every host mutation passes a Cedar check; owner set from `ic.caller()` |
| **ic-python-db entities**      | `TodoList`, `TodoItem` with relations                                  |
| **SecureEntity plug-and-play** | No hand-written Cedar/RPC/sandbox code                                 |




## Quick Start

Requires [icp-cli](https://github.com/dfinity/icp-cli) and Python 3.10+.

```bash
# Start a local IC replica
icp network start -d

# Deploy (downloads Cedar canister template, sets BASILISK_TEMPLATE_WASM)
make deploy

# Smoke test
make test
```

`make deploy` runs `scripts/setup_cedar_template.sh` to cache the Cedar WASM template
at `~/.config/basilisk/cpython_canister_template_cedar.wasm`, then deploys with
`BASILISK_TEMPLATE_WASM` pointing at that artifact.

`requirements.txt` installs `ic-basilisk-toolkit` from the parent repo (`-e ../..`)
so template development always picks up local changes. If you copy this template
outside the monorepo, replace that line with `ic-basilisk-toolkit>=0.5.0`.

## REPL usage

```bash
basilisk-toolkit shell --canister todo_list
```

Prompt shows `todo_list# ` (or a canister-id prefix when using a principal). The banner
includes Cedar enforcement status and entity counts when `status()` is available.

Inside the shell (native `ic-python-db` API, Cedar-filtered to rows you may see):

```python
lst = TodoList(title="groceries")   # last value displayed automatically
lst.public = True
TodoList.instances()
TodoList["1"]                       # None if missing; ✗ access denied if not yours
TodoList.count()
lst.title = "shopping"              # auto-save on attribute write
lst.items()
TodoItem(title="milk", todo_list=lst)
```

Magic commands (client-side, no `icp` needed):

```text
%cedar policies      # current Cedar policy source
%cedar status        # enforcement + warnings
%entities            # row counts from status()
```

Cross-user modification is denied by Cedar (`PermissionError`).

## Cedar introspection (`__cedar__`)

Read-only query endpoint (like `__browse__`, but for Cedar). Shows the schema and
policy source currently loaded — including any runtime reload via `orm.reload_policies()`.

```bash
# Full snapshot: schema, base/extra/effective policies, enforcement status
icp canister call todo_list __cedar__ '("{\"action\": \"snapshot\"}")' --query

# Policy text only
icp canister call todo_list __cedar__ '("{\"action\": \"policies\"}")' --query

# From Python client tooling
python3 -c "from ic_basilisk_toolkit.shell import canister_cedar; print(canister_cedar('policies', 'todo_list'))"
```

Actions: `snapshot` (default), `policies`, `schema`, `status`.

## Runtime custom policies (public read demo)

The template ships with **owner-only** policies at boot. List owners can load extra
Cedar at runtime to allow read access to lists marked `public`:

```bash
# Alice creates a public list
icp canister call todo_list __shell__ '("lst = TodoList.create(title='"'"'groceries'"'"'); lst.public = True")'

# Bob cannot see it yet (owner-only)
icp identity default bob
icp canister call todo_list __shell__ '("print(repr([x.title for x in TodoList.instances()]))")'
# → []

# Alice enables runtime public-read policies
icp identity default alice
icp canister call todo_list enable_public_read '(true)'

# Verify policies loaded
icp canister call todo_list __cedar__ '("{\"action\": \"policies\"}")' --query

# Bob can now read (but not write) public lists
icp identity default bob
icp canister call todo_list __shell__ '("print(repr([x.title for x in TodoList.instances()]))")'

# Disable again
icp identity default alice
icp canister call todo_list enable_public_read '(false)'
```

Custom policy source lives in `src/cedar_extra.py`. Edit it, redeploy, then toggle
with `enable_public_read` — no Cedar file bundling required at runtime.

## Two identities (cross-user test)

Uses **icp** identities (dfx identities are separate — icp identities drive the caller):

```bash
icp identity new alice
icp identity default alice
icp canister call todo_list __shell__ '("lst = TodoList.create(title='"'"'mine'"'"'); print(lst.id)")'

icp identity new bob
icp identity default bob
icp canister call todo_list __shell__ '("lst = TodoList.load('"'"'1'"'"'); lst.title = '"'"'stolen'"'"'")'
# Expect: PermissionError / Cedar denied
```



## Gotchas

- **Cedar template artifact required** — deploy fails closed without
`cpython_canister_template_cedar.wasm`. Run `make cedar-template` or let
`make deploy` download it.
- `basilisk.sandbox` **must be present** in the installed `ic-basilisk`. If the PyPI
build lacks it, install from a local checkout: `pip install -e ../../../basilisk`.
- `TERM=xterm` (or `xterm-256color`) on headless terminals — dfx/icp TUI needs
a real terminal type; `scripts/test_local.py` sets this automatically.
- **No** `json` **in the sandbox** — the subinterpreter has a restricted stdlib; use
`repr()` for debugging output inside `__shell__`.



## Security model

User Python runs in an isolated Basilisk subinterpreter. The only bridge to the host
is the ORM stub API (backed by six generic `orm.*` RPC verbs with an `_entity`
kwarg — the C sandbox gate allows at most 32 actions). Every host-side mutation passes
a Cedar authorization check. The `owner` field is always set host-side from
`ic.caller()` — sandbox code cannot forge ownership. Each `__shell__` invocation
gets a fresh REPL namespace; variables do not persist across calls.

For a step-by-step walkthrough — user-facing stub ORM (`TodoList.load`, …),
internal `rpc()` bridge, Cedar, host DB — see
[docs/SECURE_ORM.md](../../docs/SECURE_ORM.md).

## Project layout

```
src/
  main.py              — entities, setup_secure_orm(), __shell__, __cedar__, status
scripts/
  setup_cedar_template.sh
  test_local.py
icp.yaml
Makefile
requirements.txt
```

