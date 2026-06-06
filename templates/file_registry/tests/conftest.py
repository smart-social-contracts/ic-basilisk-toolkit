"""
Shared pytest fixtures for ic-file-registry integration tests.

Starts a local PocketIC replica, builds and deploys the canister once
per test session, then tears everything down.

Usage:
    pytest tests/test_integration.py -v
"""

import json
import os
import subprocess
import time

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CANISTER_NAME = "ic_file_registry"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _icp(args, cwd=REPO_ROOT, check=True, timeout=120, input=None):
    result = subprocess.run(
        ["icp"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"icp {' '.join(args)} failed:\n"
            f"stdout: {result.stdout[-500:]}\n"
            f"stderr: {result.stderr[-500:]}"
        )
    return result


def call_canister(method, args=None, update=False):
    """Call a canister method and return the parsed JSON response."""
    cmd = ["canister", "call"]
    if not update:
        cmd.append("--query")
    cmd.append(CANISTER_NAME)
    cmd.append(method)
    if args is not None:
        cmd.append(f'("{args}")')
    result = _icp(cmd)
    return _parse(result.stdout)


def _parse(output):
    """Parse icp Candid text output: (\"JSON\") -> dict/list."""
    text = output.strip()
    if text.startswith('("') and text.endswith('")'):
        inner = text[2:-2].replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")
        return json.loads(inner)
    # May be returned as unquoted text in some icp versions
    try:
        return json.loads(text.strip('()').strip('"'))
    except Exception:
        return text


# ---------------------------------------------------------------------------
# Session fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def replica():
    """Start a local replica for the test session (icp-cli)."""
    # Tolerant start: a network may already be running locally / in CI.
    _icp(["network", "start", "-d"], timeout=300, check=False)
    time.sleep(3)

    yield "local"

    _icp(["network", "stop"], check=False)


@pytest.fixture(scope="session")
def canister(replica):
    """Build and deploy ic_file_registry once for the whole test session."""
    # Build WASM
    env = os.environ.copy()
    env["CANISTER_CANDID_PATH"] = os.path.join(REPO_ROOT, "ic_file_registry.did")
    result = subprocess.run(
        ["python3", "-m", "basilisk", CANISTER_NAME, "src/main.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
    )
    if result.returncode != 0:
        pytest.fail(f"Build failed:\n{result.stderr[-1000:]}")

    # Deploy
    _icp(["deploy", CANISTER_NAME], timeout=120)

    yield CANISTER_NAME
