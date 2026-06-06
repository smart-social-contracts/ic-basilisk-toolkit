"""
Basilisk Deployer CLI — manage the on-chain WASM deployer canister.

Commands:
  basilisk deploy    Deploy a new basilisk canister from a stored WASM version
  basilisk upgrade   Upgrade an existing canister to a new WASM version
  basilisk versions  List available WASM versions on the deployer
  basilisk deployments  List deployment history
"""

import json
import subprocess
import sys

# Default deployer canister on mainnet
DEFAULT_DEPLOYER_CANISTER = "3dwln-xiaaa-aaaag-ayrva-cai"

_HELP_DEPLOY = """\
basilisk deploy — Deploy a new basilisk canister.

Usage: basilisk deploy [options]

Required:
  --version <ver>      WASM version to deploy (e.g. 0.11.25)

Options:
  --deployer <id>      Deployer canister ID  [default: {default}]
  --network <net>      Network: local, ic     [default: ic]
  --identity <name>    icp identity to use    [default: current]
  --controllers <p>    Extra controller principals (comma-separated)
  --cycles <n>         Cycles to attach       [default: 500000000000]
  --init-arg <b64>     Base64-encoded init argument for the new canister

Examples:
  basilisk deploy --version 0.11.25
  basilisk deploy --version 0.11.25 --controllers p1,p2 --network ic
""".format(default=DEFAULT_DEPLOYER_CANISTER)

_HELP_UPGRADE = """\
basilisk upgrade — Upgrade an existing canister to a new WASM version.

Usage: basilisk upgrade [options]

Required:
  --canister <id>      Canister to upgrade
  --version <ver>      Target WASM version

Options:
  --deployer <id>      Deployer canister ID  [default: {default}]
  --network <net>      Network: local, ic     [default: ic]
  --identity <name>    icp identity to use    [default: current]

Examples:
  basilisk upgrade --canister zlmui-fiaaa-aaaag-ayrza-cai --version 0.11.26
""".format(default=DEFAULT_DEPLOYER_CANISTER)

_HELP_VERSIONS = """\
basilisk versions — List available WASM versions on the deployer canister.

Usage: basilisk versions [options]

Options:
  --deployer <id>      Deployer canister ID  [default: {default}]
  --network <net>      Network: local, ic     [default: ic]
  --identity <name>    icp identity to use    [default: current]
  --json               Output raw JSON
""".format(default=DEFAULT_DEPLOYER_CANISTER)

_HELP_DEPLOYMENTS = """\
basilisk deployments — List deployment history from the deployer canister.

Usage: basilisk deployments [options]

Options:
  --deployer <id>      Deployer canister ID  [default: {default}]
  --network <net>      Network: local, ic     [default: ic]
  --identity <name>    icp identity to use    [default: current]
  --json               Output raw JSON
""".format(default=DEFAULT_DEPLOYER_CANISTER)


def _parse_candid_string(raw: str) -> str:
    """Parse a Candid text response from an icp canister call."""
    raw = raw.strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1].strip()
    # Strip trailing comma (the CLI sometimes outputs `"value",`)
    if raw.endswith(","):
        raw = raw[:-1].strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    raw = raw.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    return raw


def _icp_call(deployer, method, arg, network, identity, is_query=False, timeout=300):
    """Run an icp canister call and return parsed output."""
    cmd = ["icp", "canister", "call"]
    if identity:
        cmd.extend(["--identity", identity])
    if network:
        # The deployer is always a principal, so target it by network.
        cmd.extend(["-n", network])
    if is_query:
        cmd.append("--query")
    cmd.extend([deployer, method, arg])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            print(f"Error: {result.stderr.strip()}", file=sys.stderr)
            sys.exit(1)
        return _parse_candid_string(result.stdout)
    except subprocess.TimeoutExpired:
        print(f"Error: canister call timed out ({timeout}s)", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: icp not found. Install icp-cli.", file=sys.stderr)
        sys.exit(1)


def _parse_common_args(args):
    """Parse common flags: --deployer, --network, --identity, --json."""
    deployer = DEFAULT_DEPLOYER_CANISTER
    network = "ic"
    identity = None
    raw_json = False
    remaining = []

    i = 0
    while i < len(args):
        if args[i] == "--deployer" and i + 1 < len(args):
            deployer = args[i + 1]
            i += 2
        elif args[i] == "--network" and i + 1 < len(args):
            network = args[i + 1]
            i += 2
        elif args[i] == "--identity" and i + 1 < len(args):
            identity = args[i + 1]
            i += 2
        elif args[i] == "--json":
            raw_json = True
            i += 1
        else:
            remaining.append(args[i])
            i += 1

    return deployer, network, identity, raw_json, remaining


def _format_size(size_bytes):
    """Format byte size to human-readable string."""
    if size_bytes >= 1_000_000:
        return f"{size_bytes / 1_000_000:.1f} MB"
    elif size_bytes >= 1_000:
        return f"{size_bytes / 1_000:.1f} KB"
    return f"{size_bytes} B"


def _format_timestamp(ns):
    """Format nanosecond IC timestamp to human-readable UTC string."""
    if not ns:
        return "—"
    try:
        from datetime import datetime, timezone

        ts = int(ns) / 1e9
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    except Exception:
        return str(ns)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_deploy(args):
    """Deploy a new basilisk canister."""
    if "--help" in args or "-h" in args:
        print(_HELP_DEPLOY, end="")
        return

    deployer, network, identity, _, remaining = _parse_common_args(args)

    version = None
    controllers = []
    cycles = None
    init_arg = None

    i = 0
    while i < len(remaining):
        if remaining[i] == "--version" and i + 1 < len(remaining):
            version = remaining[i + 1]
            i += 2
        elif remaining[i] == "--controllers" and i + 1 < len(remaining):
            controllers = [c.strip() for c in remaining[i + 1].split(",") if c.strip()]
            i += 2
        elif remaining[i] == "--cycles" and i + 1 < len(remaining):
            cycles = int(remaining[i + 1])
            i += 2
        elif remaining[i] == "--init-arg" and i + 1 < len(remaining):
            init_arg = remaining[i + 1]
            i += 2
        else:
            print(f"Unknown argument: {remaining[i]}", file=sys.stderr)
            sys.exit(1)

    if not version:
        print("Error: --version is required", file=sys.stderr)
        print(
            "Usage: basilisk deploy --version <ver> [--controllers p1,p2] [--cycles N]",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build JSON payload
    payload = {"version": version}
    if controllers:
        payload["controllers"] = controllers
    if cycles is not None:
        payload["cycles"] = cycles
    if init_arg:
        payload["init_arg"] = init_arg

    payload_json = json.dumps(payload).replace('"', '\\"')

    print(f"Deploying basilisk canister...")
    print(f"  Version:    {version}")
    print(f"  Deployer:   {deployer}")
    print(f"  Network:    {network}")
    if controllers:
        print(f"  Controllers: {', '.join(controllers)}")
    if cycles:
        print(f"  Cycles:     {cycles:,}")
    print()

    raw = _icp_call(
        deployer, "deploy", f'("{payload_json}")', network, identity, timeout=600
    )

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        print(raw)
        return

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        if "canister_id" in result:
            print(
                f"  (partially created canister: {result['canister_id']})",
                file=sys.stderr,
            )
        sys.exit(1)

    print(f"✅ Canister deployed successfully!")
    print(f"  Canister ID: {result['canister_id']}")
    print(f"  Version:     {result['version']}")


def cmd_upgrade(args):
    """Upgrade an existing canister to a new WASM version."""
    if "--help" in args or "-h" in args:
        print(_HELP_UPGRADE, end="")
        return

    deployer, network, identity, _, remaining = _parse_common_args(args)

    canister = None
    version = None

    i = 0
    while i < len(remaining):
        if remaining[i] == "--canister" and i + 1 < len(remaining):
            canister = remaining[i + 1]
            i += 2
        elif remaining[i] == "--version" and i + 1 < len(remaining):
            version = remaining[i + 1]
            i += 2
        else:
            print(f"Unknown argument: {remaining[i]}", file=sys.stderr)
            sys.exit(1)

    if not canister or not version:
        print("Error: --canister and --version are required", file=sys.stderr)
        print(
            "Usage: basilisk upgrade --canister <id> --version <ver>", file=sys.stderr
        )
        sys.exit(1)

    payload = json.dumps({"canister_id": canister, "version": version}).replace(
        '"', '\\"'
    )

    print(f"Upgrading canister...")
    print(f"  Canister:   {canister}")
    print(f"  Version:    {version}")
    print(f"  Deployer:   {deployer}")
    print(f"  Network:    {network}")
    print()

    raw = _icp_call(
        deployer, "upgrade", f'("{payload}")', network, identity, timeout=600
    )

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        print(raw)
        return

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"✅ Canister upgraded successfully!")
    print(f"  Canister ID: {result['canister_id']}")
    print(f"  Version:     {result['version']}")


def cmd_versions(args):
    """List available WASM versions on the deployer canister."""
    if "--help" in args or "-h" in args:
        print(_HELP_VERSIONS, end="")
        return

    deployer, network, identity, raw_json, _ = _parse_common_args(args)

    raw = _icp_call(deployer, "list_versions", "()", network, identity, is_query=True)

    try:
        versions = json.loads(raw)
    except json.JSONDecodeError:
        print(raw)
        return

    if raw_json:
        print(json.dumps(versions, indent=2))
        return

    if not versions:
        print("No versions available.")
        return

    print(f"Available versions on {deployer}:\n")
    for v in versions:
        print(f"  {v['version']}")
        print(f"    Size:        {_format_size(v.get('size', 0))}")
        print(f"    SHA-256:     {v.get('sha256', '—')[:16]}...")
        print(f"    Description: {v.get('description', '—')}")
        print(f"    Uploaded:    {_format_timestamp(v.get('upload_timestamp'))}")
        print()


def cmd_deployments(args):
    """List deployment history from the deployer canister."""
    if "--help" in args or "-h" in args:
        print(_HELP_DEPLOYMENTS, end="")
        return

    deployer, network, identity, raw_json, _ = _parse_common_args(args)

    raw = _icp_call(
        deployer, "list_deployments", "()", network, identity, is_query=True
    )

    try:
        deployments = json.loads(raw)
    except json.JSONDecodeError:
        print(raw)
        return

    if raw_json:
        print(json.dumps(deployments, indent=2))
        return

    if not deployments:
        print("No deployments recorded yet.")
        return

    print(f"Deployment history ({len(deployments)} entries):\n")
    for d in deployments:
        action = d.get("action", "?").upper()
        print(f"  [{action}] {d.get('canister_id', '?')}")
        print(f"    Version:  {d.get('version', '?')}")
        print(f"    Caller:   {d.get('caller', '?')}")
        print(f"    Time:     {_format_timestamp(d.get('timestamp'))}")
        print()
