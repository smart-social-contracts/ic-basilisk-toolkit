"""Unit tests for shell REPL UX helpers (no live canister)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ic_basilisk_toolkit.shell import (
    _format_exec_error,
    _format_repl_error,
    _print_output,
    _repl_prompt,
    _repl_prompt_label,
    _unwrap_ic_reject,
)

# Host trap string (realms#349 / Xiao): exact permission + causing call.
_HOST_DENIED = (
    "✗ access denied: Host.set_canister_config from api.call('set_canister_config')"
)

# Realistic icp-cli + agent-rs reject wrapping that host line.
_SHELL_REJECT = f"""\
Error: Failed update call.

Caused by:
    The replica returned a rejection error: reject code CanisterError, reject message Error from Canister 4caro-hl777-77775-aaaba-cai: Canister called `ic0.trap` with message: {_HOST_DENIED}
Canister Backtrace:
  cpython_canister_template::shell::execute
  cpython_canister_template::update
, error code Some("IC0503")
"""

_DIRECT_UPDATE_REJECT = f"""\
[icp error] Error: Failed to call canister.

Caused by:
    0: direct update call failed
    1: The replica returned a rejection error: reject code CanisterError, reject message Error from Canister 4caro-hl777-77775-aaaba-cai: Canister called `ic0.trap` with message: {_HOST_DENIED}
Canister Backtrace:
  cpython_canister_template::panic
, error code Some("IC0503")
"""

_REJECT_WITH_PRINCIPAL = f"""\
direct update call failed: The replica returned a rejection error: reject code CanisterError, reject message Error from Canister 4caro-hl777-77775-aaaba-cai: Canister called `ic0.trap` with message: {_HOST_DENIED} (principal: z32zf-aaaaa-aaaaa-aaaaa-cai)
Canister Backtrace:
  cpython_canister_template::trap
, error code Some("IC0503")
"""

_QUOTED_TRAP_REJECT = (
    "The replica returned a rejection error: reject code CanisterError, "
    "reject message Error from Canister 4caro-hl777-77775-aaaba-cai: "
    f"Canister called `ic0.trap` with message: '{_HOST_DENIED}'"
)


class TestReplPrompt:
    def test_name_canister_uses_name(self):
        assert _repl_prompt_label("todo_list") == "todo_list"
        assert _repl_prompt("todo_list") == "todo_list# "

    def test_principal_uses_prefix(self):
        cid = "4caro-hl777-77775-aaaba-cai"
        assert _repl_prompt_label(cid) == "4caro"
        assert _repl_prompt(cid) == "4caro# "

    def test_continuation_prompt(self):
        assert _repl_prompt("todo_list", continuation=True) == "...     "


class TestReplErrorFormatting:
    def test_formats_nested_permission_error(self):
        raw = "sandboxed call raised: PermissionError: access denied: get on TodoList/1 (not authorized for your principal)"
        out = _format_repl_error(raw)
        assert out.startswith("✗")

    def test_does_not_double_prefix_host_access_denied(self):
        raw = "✗ access denied: call on Host (create_department)"
        assert _format_repl_error(raw) == raw


class TestIcRejectUnwrap:
    def test_unwraps_host_permission_and_causing_call(self):
        got = _unwrap_ic_reject(_SHELL_REJECT)
        assert got is not None
        assert got["host_message"] == _HOST_DENIED
        assert "cpython_canister_template" in (got["backtrace"] or "")
        assert got["inner_trap"] == _HOST_DENIED

    def test_default_is_exact_host_line(self):
        out = _format_exec_error(_SHELL_REJECT, verbose=False)
        assert out == _HOST_DENIED
        assert out.count("\n") == 0
        assert "shell.execute" not in out
        lowered = out.lower()
        assert "direct update call failed" not in lowered
        assert "replica returned a rejection" not in lowered
        assert "ic0.trap" not in lowered
        assert "backtrace" not in lowered
        assert "cpython_canister_template" not in lowered

    def test_does_not_rewrite_to_shell_execute(self):
        out = _format_exec_error(_DIRECT_UPDATE_REJECT, verbose=False)
        assert out == _HOST_DENIED
        assert out != "✗ access denied: shell.execute"

    def test_prints_similar_access_denied_exactly(self):
        raw = (
            "The replica returned a rejection error: reject code CanisterError, "
            "reject message Error from Canister 4caro-hl777-77775-aaaba-cai: "
            "Canister called `ic0.trap` with message: "
            "✗ access denied: call on Host (create_department)"
        )
        assert _format_exec_error(raw, verbose=False) == (
            "✗ access denied: call on Host (create_department)"
        )

    def test_preserves_quoted_api_call_in_host_line(self):
        assert _format_exec_error(_QUOTED_TRAP_REJECT, verbose=False) == _HOST_DENIED

    def test_verbose_adds_principal_trap_and_backtrace(self):
        out = _format_exec_error(_REJECT_WITH_PRINCIPAL, verbose=True)
        lines = out.splitlines()
        assert lines[0] == _HOST_DENIED
        assert "principal: z32zf-aaaaa-aaaaa-aaaaa-cai" in out
        assert f"trap: {_HOST_DENIED}" in out
        assert "Canister Backtrace:" in out
        assert "cpython_canister_template::trap" in out

    def test_verbose_without_principal_still_shows_trap_and_backtrace(self):
        out = _format_exec_error(_SHELL_REJECT, verbose=True)
        assert out.startswith(_HOST_DENIED)
        assert "principal:" not in out
        assert f"trap: {_HOST_DENIED}" in out
        assert "Canister Backtrace:" in out
        assert "cpython_canister_template::shell::execute" in out

    def test_print_output_default_is_one_host_line(self, capsys):
        _print_output("[icp error] " + _SHELL_REJECT)
        captured = capsys.readouterr()
        assert captured.out == _HOST_DENIED + "\n"

    def test_lacks_permission_fallback_uses_named_permission(self):
        raw = (
            "The replica returned a rejection error: reject code CanisterError, "
            "reject message Error from Canister 4caro-hl777-77775-aaaba-cai: "
            "Canister called `ic0.trap` with message: "
            "AccessDenied: lacks permission 'Host.set_canister_config'"
        )
        out = _format_exec_error(raw, verbose=False)
        assert out == "✗ access denied: Host.set_canister_config"
        assert "shell.execute" not in out

    def test_already_unwrapped_host_line_is_unchanged(self):
        assert _format_exec_error(_HOST_DENIED, verbose=False) == _HOST_DENIED
