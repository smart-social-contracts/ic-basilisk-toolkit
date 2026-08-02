"""Unit tests for shell REPL UX helpers (no live canister)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ic_basilisk_toolkit.shell import (
    _format_repl_error,
    _repl_prompt,
    _repl_prompt_label,
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
