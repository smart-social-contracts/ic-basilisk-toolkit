# ic-basilisk-toolkit

**Basilisk Toolkit** — A set of ready-made tools on top of [Basilisk](https://github.com/smart-social-contracts/basilisk) CDK for IC Python canisters.

**Live demo:** [https://ic-basilisk.tech/](https://ic-basilisk.tech/).

## Overview

<table>
<tr>
  <td><strong>Task Management</strong></td>
  <td>Create, schedule, and run background tasks</td>
</tr>
<tr><td colspan="2">

```shell
%task create heartbeat every 60s --code "print('alive')"
%task start 1
%task info 1
%task list
```
```python
t = Task(name="sync")
TaskStep(task=t, code="print('syncing...')")
TaskSchedule(task=t, interval=60)
TaskManager().run()
```
</td></tr>
<tr>
  <td><strong>Wallet</strong></td>
  <td>ICRC-1 token registry, transfers, balance tracking</td>
</tr>
<tr><td colspan="2">

```shell
%wallet balance
%wallet transfer ckBTC <principal> 1000
```
```python
w = Wallet()
w.register_token("ckBTC", ledger="mxzaz-...", indexer="n5wcd-...")
yield from w.balance_of("ckBTC")
yield from w.transfer("ckBTC", to_principal, amount=1000)
yield from w.refresh("ckBTC", max_results=50)
w.list_transfers("ckBTC", limit=20)
```
</td></tr>
<tr>
  <td><strong>Encryption</strong></td>
  <td>vetKeys + per-principal envelopes + groups</td>
</tr>
<tr><td colspan="2">

```shell
%crypto encrypt "secret"
%group create team1
%group add team1 <principal>
```
```python
cs = CryptoService(vetkey_service)
yield from cs.init_scope("my-scope")
cs.grant_access("my-scope", target_principal)
cs.create_group("team")
cs.add_member("team", principal)
cs.grant_group_access("my-scope", "team")
cs.revoke_access("my-scope", principal)
```
</td></tr>
<tr>
  <td><strong>FX</strong></td>
  <td>Exchange rate queries via the IC XRC canister</td>
</tr>
<tr><td colspan="2">

```shell
%fx ICP/USD
```
```python
fx = FXService()
fx.register_pair("ICP", "USD")
yield from fx.refresh()         # fetch latest rates from XRC canister
fx.get_rate("ICP", "USD")       # cached float
fx.get_rate_info("ICP", "USD")  # full info with staleness metadata
```
</td></tr>
<tr>
  <td><strong>Entities</strong></td>
  <td>Persistent ORM via <a href="https://github.com/smart-social-contracts/ic-python-db">ic-python-db</a></td>
</tr>
<tr><td colspan="2">

```shell
%db types
%db list User 10
%db show User 1
%db search User name=alice
%db export User backup.json
%db import backup.json
```
```python
u = User(name="alice")   # auto-persisted on creation
u.name = "bob"           # changes save automatically
bob = User['bob']
bob.friends
```
</td></tr>
<tr>
  <td><strong>Secure ORM</strong></td>
  <td>Sandboxed, Cedar-authorized entity access — plug-and-play</td>
</tr>
<tr><td colspan="2">

```python
class TodoList(SecureEntity):
    title = String(max_length=200)
    owner = String(max_length=64)

orm = setup_secure_orm([TodoList], namespace="TodoApp")

@update
def __shell__(code: str) -> text:
    return orm.shell(code)   # sandboxed REPL; Cedar checks every mutation
```

Users REPL with the native `ic-python-db` API (`TodoList.create(...)`, `TodoList.instances()`,
`TodoList.count()`, `TodoList.find({...})`, `lst.title = "x"` auto-saves) — always
Cedar-filtered to rows the caller may see. Every change crosses a host RPC gated by
Cedar owner policies. See the [todo_list template](templates/todo_list) and
[how the sandbox/Cedar/RPC pipeline works](docs/SECURE_ORM.md).

</td></tr>
<tr>
  <td><strong>Cedar Authorization</strong></td>
  <td>Policy engine wrapper, schema generation, fail-closed decision point</td>
</tr>
<tr><td colspan="2">

```python
from ic_basilisk_toolkit.cedar_engine import CedarEngine

engine = CedarEngine("TodoApp", "User", schema, policies)
engine.load()                      # fail-closed; parses + validates at startup
engine.check(caller, "entity.update", "TodoList", row_id, row)

@query
def __cedar__(query: str) -> text:
    return orm.cedar(query)   # read-only: schema, policies, status
```

Requires the Cedar canister template artifact (`_basilisk_cedar`). Policy text is tracked
in Python (the native module does not export loaded policies). Use ``reload_policies()``
to hot-swap extra permits at runtime; ``__cedar__`` shows what is currently loaded.

</td></tr>
<tr>
  <td><strong>Schema Upgrades</strong></td>
  <td>Pre-deploy schema diff, migration safety, read-only data browsing</td>
</tr>
<tr><td colspan="2">

```shell
basilisk check-upgrade --canister my_app --network ic
```

See <a href="docs/SCHEMA_UPGRADE_CHECKING.md">documentation</a>.

</td></tr>
<tr>
  <td><strong>Logging</strong></td>
  <td>Structured logging via <a href="https://github.com/smart-social-contracts/ic-python-logging">ic-python-logging</a></td>
</tr>
<tr><td colspan="2">

```python
from ic_python_logging import get_logger
logger = get_logger("my_module")
logger.info("processing")
logger.error("failed", exc_info=True)
```
</td></tr>
<tr>
  <td><strong>HTTP Fetch</strong></td>
  <td>Download a URL into the canister filesystem</td>
</tr>
<tr><td colspan="2">

```shell
%wget https://example.com/data.json /data.json
```
```python
yield from wget("https://example.com/data.json", "/data.json")
run("/data.json")  # execute a downloaded Python script
```
</td></tr>
<tr>
  <td><strong>File Transfer</strong></td>
  <td>Move files between local machine and canister</td>
</tr>
<tr><td colspan="2">

```shell
%put local_script.py /app/script.py
%get /app/output.json result.json
```
</td></tr>
<tr>
  <td><strong>Interactive Shell</strong></td>
  <td>REPL for live canister interaction</td>
</tr>
<tr><td colspan="2">

```shell
basilisk-toolkit shell --canister my_app --network ic
basilisk-toolkit exec --canister my_app 'print(ic.time())'
```
</td></tr>
<tr>
  <td><strong>SFTP</strong></td>
  <td>Browse and edit canister filesystem over SSH</td>
</tr>
<tr><td colspan="2">

```shell
basilisk-toolkit sshd --canister my_app --network ic
sftp -P 2222 localhost
ssh -p 2222 localhost
```
</td></tr>
</table>

Type `:help` inside the shell for full command reference, or see the [tip_jar template](templates/tip_jar) for a working example project.

## Templates

Full-stack example canisters, each deployable as-is:

- [**Tip Jar**](templates/tip_jar) — crypto donations in ckBTC / ckETH / ckUSDC / ICP with live exchange rates, encrypted messages, donor leaderboard. [Live](https://ox2q2-saaaa-aaaau-agj7a-cai.icp0.io/).
- [**File Registry**](templates/file_registry) — general-purpose on-chain file storage with HTTP serving, CORS, chunked upload for large WASMs, and per-namespace ACLs. [Live](https://oe3kv-3aaaa-aaaac-qgmzq-cai.icp0.io/).
- [**Todo List**](templates/todo_list) — multi-user todo lists with sandboxed REPL and Cedar owner-only authorization, built on `SecureEntity`. Backend-only; the smallest secure-ORM example.

## Website

[`website/`](website/) is the static landing page at [ic-basilisk.tech](https://ic-basilisk.tech/) — it introduces basilisk and this toolkit and links out to the live template demos. Pure assets canister, no backend.

## Installation

```bash
pip install ic-basilisk-toolkit
```

For shell/SFTP support (requires `asyncssh`):
```bash
pip install ic-basilisk-toolkit[shell]
```

## CLI Usage

```
basilisk-toolkit exec 'print("hello")'                    # Execute code on canister
basilisk-toolkit shell --canister my_app --network ic      # Interactive shell
basilisk-toolkit sshd --canister my_app --network ic       # SSH/SFTP server
```

## Schema Upgrade Checking & Data Browsing

See [docs/SCHEMA_UPGRADE_CHECKING.md](docs/SCHEMA_UPGRADE_CHECKING.md) for the full guide on pre-deploy schema diffing, step-by-step migration examples, verbose mode, on-chain enforcement, and the read-only `__browse__` query API.

> **Security note:** The `sshd` command starts a local development proxy that accepts any password. It is intended for local use only — do not expose it on a public network without adding proper authentication.

## Dependencies

- [ic-basilisk](https://github.com/smart-social-contracts/basilisk) — CDK (types, decorators, `ic.*` API)
- [ic-python-db](https://github.com/smart-social-contracts/ic-python-db) — Persistent entity ORM
- [ic-python-logging](https://github.com/smart-social-contracts/ic-python-logging) — Structured logging


## Disclaimer

**This software is not production-ready.** Do not deploy to mainnet or use with real assets or canister state you cannot afford to lose.

This software is in early development (alpha). It may contain bugs, breaking changes, and unknown security vulnerabilities. It has not undergone an independent security audit. **Use at your own risk** — see the Basilisk [disclaimer](https://github.com/smart-social-contracts/basilisk#disclaimer) for additional context.

- Not recommended for production deployments on the Internet Computer
- No guarantee of correctness, availability, or security
- APIs and behavior may change without notice

## License

MIT — see [LICENSE](LICENSE).
