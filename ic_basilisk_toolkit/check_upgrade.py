"""Check schema compatibility before deploying an upgrade.

Fetches the stored schema from a canister via __browse__, builds the
new schema from local Entity class definitions, diffs them, and prints
a report showing what changed and whether each change is safe.
"""

import json
import subprocess
import sys
from pathlib import Path


def _call_browse(
    canister: str, query: dict, network: str | None = None, identity: str | None = None
) -> dict:
    """Call __browse__ on a canister and return the parsed JSON response."""
    from ic_basilisk_toolkit.cli import _network_flags

    q_str = json.dumps(query).replace('"', r"\"")
    cmd = ["icp", "canister", "call"]
    if identity:
        cmd.extend(["--identity", identity])
    cmd.extend(_network_flags(canister, network))
    cmd.extend([canister, "__browse__", f'("{q_str}")', "--query"])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"icp call failed: {result.stderr.strip()}")

    raw = result.stdout.strip()
    # Parse Candid text response: ("...",) or (text "...")
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1].strip()
    if raw.endswith(","):
        raw = raw[:-1].strip()
    if raw.startswith("text "):
        raw = raw[5:].strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    raw = raw.replace('\\"', '"').replace("\\\\", "\\")
    return json.loads(raw)


def _load_local_schema(project_dir: str | None = None) -> dict:
    """Build the schema from local Entity class definitions.

    Imports all Python modules under the project's src/ directory,
    then builds the schema from only the newly registered Entity types
    (ignoring toolkit-internal entities loaded earlier).
    """
    import importlib
    import importlib.util

    from ic_python_db import Database, Entity

    pre_deferred_names = {t.__name__ for t in Entity._deferred_types}
    pre_instance = Database._instance
    pre_registered = set(pre_instance._entity_types.keys()) if pre_instance else set()
    pre_all = pre_registered | pre_deferred_names

    src_dir = Path(project_dir or ".") / "src"
    if not src_dir.exists():
        src_dir = Path(project_dir or ".")

    sys.path.insert(0, str(src_dir))

    for py_file in sorted(src_dir.rglob("*.py")):
        if py_file.name.startswith("_"):
            continue
        rel = py_file.relative_to(src_dir)
        module_name = str(rel).replace("/", ".").replace("\\", ".").removesuffix(".py")
        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)
        except Exception:
            pass

    from ic_python_db.schema import build_schema

    db = Database._instance or Database.get_instance()
    new_types = {k: v for k, v in db._entity_types.items() if k not in pre_all}
    return build_schema(new_types)


def _format_change(change) -> str:
    """Format a SchemaChange for terminal output."""
    icon = "\u2705" if change.safe else "\u26a0\ufe0f "
    loc = f"{change.entity_type}.{change.field}" if change.field else change.entity_type
    return f"  {icon}  {loc}: {change.reason}"


def _print_verbose_schemas(
    old_schema: dict, new_schema: dict, output_dir: str | None = None
):
    """Print both schemas side-by-side and write them to files for manual comparison."""
    schema_dir = Path(output_dir) if output_dir else Path(".basilisk/schemas")
    schema_dir.mkdir(parents=True, exist_ok=True)

    old_path = schema_dir / "on_chain_schema.json"
    new_path = schema_dir / "local_schema.json"

    old_pretty = json.dumps(old_schema, indent=2, sort_keys=True)
    new_pretty = json.dumps(new_schema, indent=2, sort_keys=True)

    old_path.write_text(old_pretty + "\n")
    new_path.write_text(new_pretty + "\n")

    print(f"\n--- On-chain schema ({old_path}) ---")
    print(old_pretty)
    print(f"\n--- Local schema ({new_path}) ---")
    print(new_pretty)
    print(f"\nSchemas written to {schema_dir}/ for manual comparison.")
    print(f"  diff {old_path} {new_path}\n")


def cmd_check_upgrade(args: list[str]):
    """Check schema compatibility before upgrading a canister."""
    canister = None
    network = None
    identity = None
    project_dir = None
    verbose = False
    output_dir = None

    i = 0
    while i < len(args):
        if args[i] in ("-h", "--help"):
            print(_HELP_CHECK_UPGRADE, end="")
            return
        elif args[i] in ("--verbose", "-v"):
            verbose = True
            i += 1
        elif args[i] == "--output-dir" and i + 1 < len(args):
            verbose = True
            output_dir = args[i + 1]
            i += 2
        elif args[i] == "--canister" and i + 1 < len(args):
            canister = args[i + 1]
            i += 2
        elif args[i] == "--network" and i + 1 < len(args):
            network = args[i + 1]
            i += 2
        elif args[i] == "--identity" and i + 1 < len(args):
            identity = args[i + 1]
            i += 2
        elif args[i] == "--project" and i + 1 < len(args):
            project_dir = args[i + 1]
            i += 2
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    if not canister:
        from ic_basilisk_toolkit.cli import _detect_canister_from_icp

        canister = _detect_canister_from_icp()
        if not canister:
            print(
                "Error: --canister required (could not auto-detect from icp.yaml)",
                file=sys.stderr,
            )
            sys.exit(1)

    # 1. Fetch the on-chain schema
    print(f"Fetching schema from canister '{canister}'...")
    try:
        browse_result = _call_browse(
            canister, {"action": "schema"}, network=network, identity=identity
        )
    except Exception as e:
        print(f"Error fetching schema: {e}", file=sys.stderr)
        sys.exit(1)

    old_schema = browse_result.get("entities")
    on_chain_hash = browse_result.get("schema_hash")

    if old_schema is None:
        print("Canister does not expose entity schema via __browse__.")
        print("Make sure the canister uses ic-python-db and basilisk >= 0.12.1.")
        sys.exit(1)

    # 2. Build the local schema
    print("Building schema from local Entity definitions...")
    try:
        new_schema = _load_local_schema(project_dir)
    except Exception as e:
        print(f"Error building local schema: {e}", file=sys.stderr)
        sys.exit(1)

    # 2b. Verbose: dump schemas
    if verbose:
        _print_verbose_schemas(old_schema, new_schema, output_dir=output_dir)

    # 3. Diff
    from ic_python_db.schema import _has_custom_migrate, diff_schemas, schema_hash

    changes = diff_schemas(old_schema, new_schema)

    if not changes:
        print(f"\nNo schema changes detected. Safe to upgrade.")
        sys.exit(0)

    # 4. Report
    safe_count = sum(1 for c in changes if c.safe)
    breaking_count = len(changes) - safe_count

    print(f"\nSchema changes ({len(changes)} total):\n")
    for change in changes:
        print(_format_change(change))

    # Check which breaking changes have migrate()
    if breaking_count > 0:
        print(f"\n{breaking_count} breaking change(s) detected.")
        from ic_python_db import Database

        db = Database.get_instance()
        missing_migrate = []
        for change in changes:
            if change.safe:
                continue
            entity_cls = db._entity_types.get(change.entity_type)
            if entity_cls and _has_custom_migrate(entity_cls):
                print(f"  {change.entity_type}: migrate() found")
            else:
                missing_migrate.append(change)
                print(f"  {change.entity_type}: migrate() MISSING")

        if missing_migrate:
            print(
                f"\nUpgrade will be REJECTED: "
                f"{len(missing_migrate)} breaking change(s) without migrate()."
            )
            sys.exit(1)
        else:
            print(f"\nAll breaking changes have migrate(). Safe to upgrade.")
    else:
        print(f"\nAll {safe_count} change(s) are safe. No migration needed.")

    # 5. Hash check
    if on_chain_hash:
        local_hash = schema_hash(new_schema)
        if on_chain_hash == local_hash:
            print(f"\nSchema hash: unchanged (canister already at this version)")
        else:
            print(f"\nSchema hash: {on_chain_hash[:16]}... -> {local_hash[:16]}...")

    sys.exit(0)


_HELP_CHECK_UPGRADE = """\
basilisk check-upgrade — Check schema compatibility before upgrading.

Usage: basilisk check-upgrade [options]

Fetches the stored schema from a canister and compares it against your
local Entity class definitions. Reports which changes are safe (auto-
migratable) and which require a migrate() method.

Options:
  --canister <id>   Canister name or principal ID  [auto-detect from icp.yaml]
  --network <net>   Network: local, ic, or URL     [default: local]
  --identity <name> icp identity to use            [default: current identity]
  --project <dir>   Project directory with src/     [default: current dir]
  -v, --verbose     Print full schemas and write them to .basilisk/schemas/
  --output-dir <d>  Directory to write schema files  [default: .basilisk/schemas/]

Examples:
  basilisk check-upgrade                            Auto-detect canister
  basilisk check-upgrade --canister my_app          Explicit canister
  basilisk check-upgrade --network ic               Check against mainnet
  basilisk check-upgrade -v                         Show full schema details
"""
