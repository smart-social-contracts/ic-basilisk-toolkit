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

## REPL usage

```bash
basilisk-toolkit shell --canister todo_list
```

Inside the shell (native `ic-python-db` API, Cedar-filtered to rows you may see):

```python
lst = TodoList.create(title="groceries")
TodoList.instances()            # your lists (alias: TodoList.mine())
TodoList.count()                # caller-scoped count, no rows transferred
TodoList.load_some(from_id=1, count=50)
lst.title = "shopping"          # auto-save on attribute write
lst.items()
TodoItem.create(title="milk", todo_list=lst)
TodoItem.find({"done": False})  # host-side equality filter
```

Cross-user modification is denied by Cedar (`PermissionError`).

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
is the ORM stub API (backed by `rpc()` internally). Every host-side mutation passes
a Cedar authorization check. The `owner` field is always set host-side from
`ic.caller()` — sandbox code cannot forge ownership.

## Project layout

```
src/
  main.py              — entities, setup_secure_orm(), __shell__, status
scripts/
  setup_cedar_template.sh
  test_local.py
icp.yaml
Makefile
requirements.txt
```

