"""
Basilisk Toolkit — CLI for canister interaction.

Usage: basilisk-toolkit <command> [options]

Commands:
  exec <code>      Execute Python code on a deployed canister
  shell            Interactive Python shell on a deployed canister
  sshd             Start an SSH/SFTP server proxy to a canister
  deploy           Deploy a new basilisk canister from the on-chain deployer
  upgrade          Upgrade an existing canister to a new WASM version
  check-upgrade    Check schema compatibility before upgrading
  versions         List available WASM versions on the deployer
  deployments      List deployment history

Options (exec, shell, sshd):
  --canister <id>   Canister name or principal ID  [auto-detect from icp.yaml]
  --network <net>   Network: local, ic, or URL     [default: local]
  --identity <name> icp identity to use            [default: current identity]

Other:
  --version        Print version info
  help, -h         Show this help

Run basilisk-toolkit <command> --help for command-specific options and examples.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_HELP_EXEC = """\
basilisk-toolkit exec — Execute Python code on a deployed canister.

Usage: basilisk-toolkit exec [options] <code>
       basilisk-toolkit exec [options] -f <file>
       echo "code" | basilisk-toolkit exec [options]

Options:
  --canister <id>  Canister name or principal ID  [auto-detect from icp.yaml]
  --network <net>  Network: local, ic, or URL     [default: local]
  -f <file>        Execute a local Python file instead of inline code

Examples:
  basilisk-toolkit exec 'print("hello")'                         Inline code
  basilisk-toolkit exec --canister my_app 'print(1+1)'           Explicit canister
  basilisk-toolkit exec --network ic 'print(ic.time())'          On mainnet
  basilisk-toolkit exec -f script.py                             Run a local file
  echo "import sys; print(sys.version)" | basilisk-toolkit exec  Pipe from stdin
"""


def _is_principal(ident: str) -> bool:
    """True if ident looks like a canister principal (vs an icp.yaml name)."""
    return bool(re.match(r"^[a-z0-9]{5}(-[a-z0-9]{5})+-[a-z0-9]{3}$", ident or ""))


def _network_flags(canister: str, network: str | None) -> list[str]:
    """icp network selection: principals use -n <network>, names use -e <env>."""
    if not network:
        return []
    return ["-n", network] if _is_principal(canister) else ["-e", network]


def _detect_canister_from_icp() -> str | None:
    """Try to find the first basilisk canister name from icp.yaml.

    Prefers a canister installed from a Basilisk-built WASM (`.basilisk/` path);
    otherwise falls back to the first declared canister.
    """
    path = Path("icp.yaml")
    if not path.exists():
        return None
    try:
        text = path.read_text()
    except OSError:
        return None
    names: list[str] = []
    current: str | None = None
    basilisk_name: str | None = None
    for line in text.splitlines():
        m = re.match(r"\s*-\s*name:\s*['\"]?([A-Za-z0-9_-]+)", line)
        if m:
            current = m.group(1)
            names.append(current)
        elif current and ".basilisk/" in line and basilisk_name is None:
            basilisk_name = current
    return basilisk_name or (names[0] if names else None)


def _parse_candid_string(raw: str) -> str:
    """Parse a Candid text response from an icp canister call."""
    raw = raw.strip()
    # Remove outer parens: (text "...")
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1].strip()
    # Remove 'text' prefix if present
    if raw.startswith("text "):
        raw = raw[5:].strip()
    # Remove surrounding quotes
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    # Unescape
    raw = raw.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    return raw


def cmd_exec(args: list[str]):
    """Execute Python code on a deployed basilisk canister."""
    canister = None
    network = None
    identity = None
    file_path = None
    code_parts = []

    i = 0
    while i < len(args):
        if args[i] == "--canister" and i + 1 < len(args):
            canister = args[i + 1]
            i += 2
        elif args[i] == "--network" and i + 1 < len(args):
            network = args[i + 1]
            i += 2
        elif args[i] == "--identity" and i + 1 < len(args):
            identity = args[i + 1]
            i += 2
        elif args[i] == "-f" and i + 1 < len(args):
            file_path = args[i + 1]
            i += 2
        else:
            code_parts.append(args[i])
            i += 1

    # Get code from file or args
    if file_path:
        try:
            code = Path(file_path).read_text()
        except FileNotFoundError:
            print(f"Error: file not found: {file_path}", file=sys.stderr)
            sys.exit(1)
    elif code_parts:
        code = " ".join(code_parts)
    else:
        # Read from stdin
        code = sys.stdin.read()

    if not code.strip():
        print(
            "Error: no code provided. Usage: basilisk-toolkit exec [--canister <c>] [--network <n>] [-f <file>] <code>",
            file=sys.stderr,
        )
        sys.exit(1)

    # Auto-detect canister if not specified
    if not canister:
        canister = _detect_canister_from_icp()
        if not canister:
            print(
                "Error: --canister required (could not auto-detect from icp.yaml)",
                file=sys.stderr,
            )
            sys.exit(1)

    # Build icp command
    escaped_code = code.replace('"', '\\"').replace("\n", "\\n")
    cmd = ["icp", "canister", "call"]
    if identity:
        cmd.extend(["--identity", identity])
    cmd.extend(_network_flags(canister, network))
    cmd.extend([canister, "__shell__", f'("{escaped_code}")'])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(result.stderr.strip(), file=sys.stderr)
            sys.exit(1)
        output = _parse_candid_string(result.stdout)
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
    except subprocess.TimeoutExpired:
        print("Error: canister call timed out (120s)", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: icp not found. Install icp-cli.", file=sys.stderr)
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    command = sys.argv[1]

    if command == "exec":
        if "--help" in sys.argv[2:] or "-h" in sys.argv[2:]:
            print(_HELP_EXEC, end="")
            return
        cmd_exec(sys.argv[2:])

    elif command == "shell":
        from ic_basilisk_toolkit.shell import main as shell_main

        sys.argv = ["basilisk-toolkit-shell"] + sys.argv[2:]
        shell_main()

    elif command == "sshd":
        from ic_basilisk_toolkit.sshd import main as sshd_main

        sys.argv = ["basilisk-toolkit-sshd"] + sys.argv[2:]
        sshd_main()

    elif command == "deploy":
        from ic_basilisk_toolkit.deployer import cmd_deploy

        cmd_deploy(sys.argv[2:])

    elif command == "upgrade":
        from ic_basilisk_toolkit.deployer import cmd_upgrade

        cmd_upgrade(sys.argv[2:])

    elif command == "check-upgrade":
        from ic_basilisk_toolkit.check_upgrade import cmd_check_upgrade

        cmd_check_upgrade(sys.argv[2:])

    elif command == "versions":
        from ic_basilisk_toolkit.deployer import cmd_versions

        cmd_versions(sys.argv[2:])

    elif command == "deployments":
        from ic_basilisk_toolkit.deployer import cmd_deployments

        cmd_deployments(sys.argv[2:])

    elif command in ("-h", "--help", "help"):
        print(__doc__.strip())

    elif command == "--version":
        from ic_basilisk_toolkit import __version__

        print(__version__)

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(__doc__.strip())
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry-point handlers for basilisk CLI plugin discovery
# (registered via pyproject.toml [project.entry-points."basilisk.commands"])
# ---------------------------------------------------------------------------


def plugin_shell():
    """basilisk shell — Interactive Python shell on a deployed canister."""
    from ic_basilisk_toolkit.shell import main as shell_main

    sys.argv = ["basilisk shell"] + sys.argv[2:]
    shell_main()


def plugin_exec():
    """basilisk exec — Execute Python code on a deployed canister."""
    if "--help" in sys.argv[2:] or "-h" in sys.argv[2:]:
        print(_HELP_EXEC, end="")
        return
    cmd_exec(sys.argv[2:])


def plugin_sshd():
    """basilisk sshd — Start an SSH/SFTP server proxy to a canister."""
    from ic_basilisk_toolkit.sshd import main as sshd_main

    sys.argv = ["basilisk sshd"] + sys.argv[2:]
    sshd_main()


def plugin_deploy():
    """basilisk deploy — Deploy a new basilisk canister."""
    from ic_basilisk_toolkit.deployer import cmd_deploy

    cmd_deploy(sys.argv[2:])


def plugin_upgrade():
    """basilisk upgrade — Upgrade an existing canister."""
    from ic_basilisk_toolkit.deployer import cmd_upgrade

    cmd_upgrade(sys.argv[2:])


def plugin_check_upgrade():
    """basilisk check-upgrade — Check schema compatibility before upgrading."""
    from ic_basilisk_toolkit.check_upgrade import cmd_check_upgrade

    cmd_check_upgrade(sys.argv[2:])


def plugin_versions():
    """basilisk versions — List available WASM versions."""
    from ic_basilisk_toolkit.deployer import cmd_versions

    cmd_versions(sys.argv[2:])


def plugin_deployments():
    """basilisk deployments — List deployment history."""
    from ic_basilisk_toolkit.deployer import cmd_deployments

    cmd_deployments(sys.argv[2:])


if __name__ == "__main__":
    main()
