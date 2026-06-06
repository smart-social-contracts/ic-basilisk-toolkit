"""Unit tests for ic_basilisk_toolkit.check_upgrade — schema compatibility CLI.

These are pure-Python unit tests (no canister required).
Run: pytest tests/test_check_upgrade.py -v
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from ic_python_db.schema import diff_schemas, schema_hash  # noqa: F401

    _has_ic_python_db_schema = True
except (ImportError, ModuleNotFoundError):
    _has_ic_python_db_schema = False

_needs_ic_python_db = pytest.mark.skipif(
    not _has_ic_python_db_schema,
    reason="ic_python_db with schema module not installed (needs >= 0.8.0)",
)

from ic_basilisk_toolkit.check_upgrade import (
    _call_browse,
    _format_change,
    _load_local_schema,
    cmd_check_upgrade,
)

# ---------------------------------------------------------------------------
# _call_browse
# ---------------------------------------------------------------------------


class TestCallBrowse:
    """Test the icp canister call wrapper."""

    def test_basic_call_structure(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '("{\\"stable_maps\\": {}}")'

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = _call_browse("my_canister", {"action": "schema"})

        args = mock_run.call_args
        cmd = args[0][0]
        assert "icp" in cmd
        assert "canister" in cmd
        assert "call" in cmd
        assert "my_canister" in cmd
        assert "__browse__" in cmd

    def test_with_network(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '("{\\"ok\\": true}")'

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _call_browse("c", {"action": "schema"}, network="ic")

        cmd = mock_run.call_args[0][0]
        # "c" is a canister name, so icp targets it by environment (-e).
        assert "-e" in cmd
        assert "ic" in cmd

    def test_with_identity(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '("{\\"ok\\": true}")'

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _call_browse("c", {"action": "schema"}, identity="alice")

        cmd = mock_run.call_args[0][0]
        assert "--identity" in cmd
        assert "alice" in cmd

    def test_raises_on_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "canister not found"

        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="icp call failed"):
                _call_browse("bad_canister", {"action": "schema"})

    def test_parses_candid_text_response(self):
        schema = {"stable_maps": {"users": {"key_type": "text"}}}
        encoded = json.dumps(schema).replace('"', '\\"')
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = f'("{encoded}")'

        with patch("subprocess.run", return_value=mock_result):
            result = _call_browse("c", {"action": "schema"})

        assert result == schema

    def test_parses_text_prefix_response(self):
        schema = {"entities": {}}
        encoded = json.dumps(schema).replace('"', '\\"')
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = f'(text "{encoded}")'

        with patch("subprocess.run", return_value=mock_result):
            result = _call_browse("c", {"action": "schema"})

        assert result == schema


# ---------------------------------------------------------------------------
# _format_change
# ---------------------------------------------------------------------------


class TestFormatChange:
    """Test change formatting for terminal output."""

    def test_safe_change_shows_checkmark(self):
        change = MagicMock()
        change.safe = True
        change.entity_type = "User"
        change.field = "email"
        change.reason = "New field with default"
        result = _format_change(change)
        assert "\u2705" in result
        assert "User.email" in result

    def test_breaking_change_shows_warning(self):
        change = MagicMock()
        change.safe = False
        change.entity_type = "User"
        change.field = "age"
        change.reason = "Type changed"
        result = _format_change(change)
        assert "\u26a0" in result
        assert "User.age" in result

    def test_entity_level_change_no_field(self):
        change = MagicMock()
        change.safe = True
        change.entity_type = "NewEntity"
        change.field = None
        change.reason = "New entity type"
        result = _format_change(change)
        assert "NewEntity" in result
        assert "." not in result.split("NewEntity")[1].split(":")[0]


# ---------------------------------------------------------------------------
# cmd_check_upgrade — CLI integration
# ---------------------------------------------------------------------------


class TestCmdCheckUpgrade:
    """Test the CLI command logic."""

    def test_help_flag(self, capsys):
        cmd_check_upgrade(["-h"])
        captured = capsys.readouterr()
        assert (
            "check-upgrade" in captured.out.lower() or "check" in captured.out.lower()
        )
        assert "canister" in captured.out.lower()

    def test_help_long_flag(self, capsys):
        cmd_check_upgrade(["--help"])
        captured = capsys.readouterr()
        assert "Usage" in captured.out or "usage" in captured.out

    def test_unknown_arg_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            cmd_check_upgrade(["--bogus"])
        assert exc_info.value.code == 1

    def test_no_canister_no_icp_yaml_exits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("ic_basilisk_toolkit.check_upgrade._call_browse") as mock_browse:
            # _detect_canister_from_icp will fail, so it should exit
            with pytest.raises(SystemExit) as exc_info:
                cmd_check_upgrade([])
            assert exc_info.value.code == 1

    def test_no_schema_in_browse_response_exits(self):
        browse_response = {"stable_maps": {"users": {}}}

        with patch(
            "ic_basilisk_toolkit.check_upgrade._call_browse",
            return_value=browse_response,
        ):
            with pytest.raises(SystemExit) as exc_info:
                cmd_check_upgrade(["--canister", "test_canister"])
            assert exc_info.value.code == 1

    @_needs_ic_python_db
    def test_no_changes_exits_zero(self):
        old_schema = {
            "User": {
                "version": 1,
                "fields": {
                    "name": {"kind": "property", "type": "String", "default": ""}
                },
                "relationships": {},
            }
        }
        browse_response = {
            "entities": old_schema,
            "schema_hash": "abc123",
            "stable_maps": {},
        }

        with (
            patch(
                "ic_basilisk_toolkit.check_upgrade._call_browse",
                return_value=browse_response,
            ),
            patch(
                "ic_basilisk_toolkit.check_upgrade._load_local_schema",
                return_value=old_schema,
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                cmd_check_upgrade(["--canister", "test_canister"])
            assert exc_info.value.code == 0

    @_needs_ic_python_db
    def test_safe_changes_exit_zero(self):
        old_schema = {
            "User": {
                "version": 1,
                "fields": {
                    "name": {"kind": "property", "type": "String", "default": ""}
                },
                "relationships": {},
            }
        }
        new_schema = {
            "User": {
                "version": 1,
                "fields": {
                    "name": {"kind": "property", "type": "String", "default": ""},
                    "email": {"kind": "property", "type": "String", "default": ""},
                },
                "relationships": {},
            }
        }
        browse_response = {"entities": old_schema, "schema_hash": "abc"}

        with (
            patch(
                "ic_basilisk_toolkit.check_upgrade._call_browse",
                return_value=browse_response,
            ),
            patch(
                "ic_basilisk_toolkit.check_upgrade._load_local_schema",
                return_value=new_schema,
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                cmd_check_upgrade(["--canister", "test_canister"])
            assert exc_info.value.code == 0

    @_needs_ic_python_db
    def test_breaking_change_without_migrate_exits_one(self):
        old_schema = {
            "User": {
                "version": 1,
                "fields": {"age": {"kind": "property", "type": "Integer"}},
                "relationships": {},
            }
        }
        new_schema = {
            "User": {
                "version": 1,
                "fields": {"age": {"kind": "property", "type": "String"}},
                "relationships": {},
            }
        }
        browse_response = {"entities": old_schema, "schema_hash": "abc"}

        # Need a mock Database with a User entity that lacks migrate()
        mock_db = MagicMock()
        mock_db._entity_types = {"User": type("User", (), {})}

        with (
            patch(
                "ic_basilisk_toolkit.check_upgrade._call_browse",
                return_value=browse_response,
            ),
            patch(
                "ic_basilisk_toolkit.check_upgrade._load_local_schema",
                return_value=new_schema,
            ),
            patch(
                "ic_python_db.Database.get_instance",
                return_value=mock_db,
            ),
            patch(
                "ic_python_db.schema._has_custom_migrate",
                return_value=False,
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                cmd_check_upgrade(["--canister", "test_canister"])
            assert exc_info.value.code == 1

    def test_browse_failure_exits_one(self):
        with patch(
            "ic_basilisk_toolkit.check_upgrade._call_browse",
            side_effect=RuntimeError("connection refused"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                cmd_check_upgrade(["--canister", "test_canister"])
            assert exc_info.value.code == 1
