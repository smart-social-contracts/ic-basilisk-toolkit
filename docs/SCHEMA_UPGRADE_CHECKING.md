# Schema Upgrade Checking

The `check-upgrade` command compares the on-chain schema with your local Entity definitions before you deploy, catching breaking changes early:

```bash
basilisk check-upgrade --canister my_app --network local
```

## Example: Full Test Workflow

```python
# src/main.py
from basilisk import query, update, text, nat64, void, init, ic, StableBTreeMap
from ic_python_db import Database, Entity
from ic_python_db.properties import String, Integer

__basilisk_features__ = ["browse"]

storage = StableBTreeMap[str, str](memory_id=1, max_key_size=100, max_value_size=10000)
db = Database.init(db_storage=storage)


class User(Entity):
    __db__ = db
    name = String(default="")
    age = Integer(default=0)


@init
def init_() -> void:
    db.save_schema()
    User(name="alice", age=30)
```

**Step 1 — No changes (clean):**

```
$ basilisk check-upgrade --canister my_app --network local
No schema changes detected. Safe to upgrade.
```

**Step 2 — Add a field with a default (safe):**

```python
class User(Entity):
    __db__ = db
    name = String(default="")
    age = Integer(default=0)
    email = String(default="unknown@example.com")  # new field
```

```
$ basilisk check-upgrade --canister my_app --network local
Schema changes (1 total):
  ✅  User.email: New field with default='unknown@example.com' — auto-migratable
All 1 change(s) are safe. No migration needed.
```

**Step 3 — Change a field type (breaking):**

```python
from ic_python_db.properties import String, Float

class User(Entity):
    __db__ = db
    name = String(default="")
    age = Float(default=0.0)   # was Integer
    email = String(default="unknown@example.com")
```

```
$ basilisk check-upgrade --canister my_app --network local
Schema changes (2 total):
  ✅  User.email: New field with default='unknown@example.com' — auto-migratable
  ⚠️   User.age: Type changed Integer → Float — requires migrate()
Upgrade will be REJECTED: 1 breaking change(s) without migrate().
```

**Step 4 — Add a `migrate()` method (fix the breaking change):**

```python
class User(Entity):
    __db__ = db
    __version__ = 2
    name = String(default="")
    age = Float(default=0.0)
    email = String(default="unknown@example.com")

    @classmethod
    def migrate(cls, data: dict, old_version: int, new_version: int) -> dict:
        if old_version < 2:
            data["age"] = float(data.get("age", 0))
        return data
```

```
$ basilisk check-upgrade --canister my_app --network local
Schema changes (3 total):
  ✅  User: Version 1 → 2
  ✅  User.email: New field with default='unknown@example.com' — auto-migratable
  ⚠️   User.age: Type changed Integer → Float — requires migrate()
All breaking changes have migrate(). Safe to upgrade.
```

## Verbose Mode

Use `--verbose` (or `-v`) to print the full schemas and save them to files for manual comparison:

```
$ basilisk check-upgrade --canister my_app --network local -v
--- On-chain schema (.basilisk/schemas/on_chain_schema.json) ---
{ ... }

--- Local schema (.basilisk/schemas/local_schema.json) ---
{ ... }

Schemas written to .basilisk/schemas/ for manual comparison.
  diff .basilisk/schemas/on_chain_schema.json .basilisk/schemas/local_schema.json
```

Use `--output-dir <dir>` to write schema files to a custom location.

## On-chain Enforcement

Even if you skip `check-upgrade`, Basilisk auto-injects a safety net into `post_upgrade`: if a breaking change is deployed without `migrate()`, the IC traps and atomically rolls back — your canister stays on the old code with all data intact.

```
$ icp deploy
...
🎉 Built canister my_app
...
Error: Failed to install wasm module to canister 'my_app'.
Caused by: Canister called `ic0.trap` with message:
  'Upgrade rejected: 1 breaking change(s) without migrate() method:
    - User.age: Type changed Integer → Float — requires migrate()'
```

The upgrade is rejected, the canister remains on the previous version, and no data is lost.

## Data Browsing (read-only)

Canisters with `__basilisk_features__ = ["browse"]` expose a `__browse__` query endpoint for instant, free, read-only data access:

```python
from ic_basilisk_toolkit.shell import canister_browse, canister_schema, canister_keys, canister_get

# Get the canister's data schema
schema = canister_schema("my_canister", network="ic")

# List keys in a stable map (paginated)
keys = canister_keys("my_canister", "users", network="ic")

# Read a single value
value = canister_get("my_canister", "users", "alice", network="ic")

# Generic browse with any action
result = canister_browse("items", "my_canister", network="ic", map="users", limit=50)
```

Unlike `__shell__` (which is an `@update` call requiring consensus and controller access), `__browse__` is a `@query` — instant response, no cycles cost, and public by default.
