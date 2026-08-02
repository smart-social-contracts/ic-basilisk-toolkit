"""Unit tests for ic_basilisk_toolkit.cedar_engine.

Run: pytest tests/test_cedar_engine.py -v
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ic_basilisk_toolkit import cedar
from ic_basilisk_toolkit.cedar_engine import CedarEngine
from ic_basilisk_toolkit.cedar_slicing import Slicer

TEST_SCHEMA = """
namespace TodoApp {
    entity TodoItem {
        done?: Bool,
        title?: String,
        todo_list?: TodoList,
    };
    entity TodoList {
        owner?: String,
        title?: String,
    };
    entity User {
        id?: String,
    };
    action read appliesTo { principal: [User], resource: [TodoItem, TodoList, User] };
    action write appliesTo { principal: [User], resource: [TodoItem, TodoList, User] };
    action "entity.create" in [write] appliesTo { principal: [User], resource: [TodoItem, TodoList, User] };
    action "entity.get" in [read] appliesTo { principal: [User], resource: [TodoItem, TodoList, User] };
    action "entity.update" in [write] appliesTo { principal: [User], resource: [TodoItem, TodoList, User] };
}
"""

TEST_POLICIES = """
permit(principal, action, resource);
"""


class FakeModule:
    """Stands in for _basilisk_cedar, recording what it was handed."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def _respond(self, name, args):
        self.calls.append((name, args))
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)

    def load(self, *args):
        return self._respond("load", args)

    def is_authorized(self, *args):
        return self._respond("is_authorized", args)

    def authorize_many(self, *args):
        return self._respond("authorize_many", args)


@pytest.fixture
def slicer():
    return Slicer("TodoApp", TEST_SCHEMA)


@pytest.fixture
def engine(slicer):
    return CedarEngine(
        namespace="TodoApp",
        principal_type="User",
        schema=TEST_SCHEMA,
        policies=TEST_POLICIES,
        slicer=slicer,
        context_provider=lambda: {"source": "test"},
    )


@pytest.fixture
def fake(monkeypatch):
    def install(reply):
        module = FakeModule(reply)
        monkeypatch.setattr(cedar, "_module", lambda: module)
        return module

    return install


class TestAvailability:
    def test_unavailable_when_native_module_missing(self, engine, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", None)
        assert engine.available() is False

    def test_available_when_native_module_present(self, engine, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        assert engine.available() is True


class TestLoad:
    def test_load_fail_closed_on_error_envelope(self, engine, fake, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        fake({"error": "validation: bad policy"})
        assert engine.load() is False
        assert engine.enabled() is False
        assert "bad policy" in engine.status()["error"]

    def test_load_success_enables_engine(self, engine, fake, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        fake({"ok": True, "warnings": ["policy0: blanket permit"]})
        assert engine.load() is True
        assert engine.enabled() is True
        assert "blanket permit" in engine.status()["warnings"][0]

    def test_load_tracks_extra_policies(self, engine, fake, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        module = fake({"ok": True, "warnings": []})
        extra = 'permit(principal, action, resource);'
        assert engine.load(extra) is True
        assert engine.extra_policies() == extra
        assert extra in engine.effective_policies()
        assert module.calls[-1][1][1] == engine.effective_policies()


class TestPolicySnapshot:
    def test_effective_policies_without_extra(self, engine):
        assert engine.effective_policies() == TEST_POLICIES

    def test_snapshot_includes_schema_and_policies(self, engine, fake, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        fake({"ok": True, "warnings": []})
        engine.load("// extra\npermit(principal, action, resource);")
        snap = engine.snapshot()
        assert snap["namespace"] == "TodoApp"
        assert snap["schema"] == TEST_SCHEMA
        assert snap["base_policies"] == TEST_POLICIES
        assert snap["extra_policies"].startswith("// extra")
        assert snap["has_extra_policies"] is True
        assert "permit(principal, action, resource);" in snap["policies"]
    def test_parses_action_names(self, engine):
        assert engine.declared_actions() == frozenset(
            {"read", "write", "entity.create", "entity.get", "entity.update"}
        )


class TestIsAuthorized:
    def test_false_when_not_enabled(self, engine):
        assert engine.is_authorized("alice", "read") is False

    def test_passes_correct_uids_and_entities(self, engine, fake, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        module = fake({"ok": True, "warnings": []})
        engine.load()
        module.reply = {"decision": "allow"}

        class Row:
            title = "Task"

        assert engine.is_authorized(
            "alice", "entity.get", "TodoItem", "1", resource_row=Row()
        )

        assert module.calls[-1][0] == "is_authorized"
        _, args = module.calls[-1]
        assert args[0] == 'TodoApp::User::"alice"'
        assert args[1] == 'TodoApp::Action::"entity.get"'
        assert args[2] == 'TodoApp::TodoItem::"1"'
        entities = json.loads(args[3])
        assert any(e["uid"]["id"] == "alice" for e in entities)
        assert any(e["uid"]["id"] == "1" for e in entities)
        assert args[4] == '{"source": "test"}'

    def test_synthetic_resource_when_type_or_id_missing(self, engine, fake, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        module = fake({"ok": True, "warnings": []})
        engine.load()
        module.reply = {"decision": "deny"}
        engine.is_authorized("alice", "read")
        _, args = module.calls[-1]
        assert args[2] == 'TodoApp::TodoApp::"todoapp"'

    def test_typed_placeholder_when_resource_id_empty(self, engine, fake, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        module = fake({"ok": True, "warnings": []})
        engine.load()
        module.reply = {"decision": "allow"}
        engine.is_authorized("alice", "entity.list", "UserProfile", "")
        _, args = module.calls[-1]
        assert args[2] == 'TodoApp::UserProfile::"_"'

    def test_cedar_error_is_fail_closed(self, engine, fake, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        fake({"error": "entities: unknown attribute"})
        fake({"ok": True, "warnings": []})
        engine.load()
        assert engine.is_authorized("alice", "read", "TodoItem", "1") is False


class TestCheck:
    def test_raises_on_deny(self, engine, fake, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        fake({"decision": "deny"})
        fake({"ok": True, "warnings": []})
        engine.load()
        with pytest.raises(PermissionError, match="'read' denied by policy"):
            engine.check("alice", "read", "TodoItem", "1")

    def test_raises_when_not_enabled_by_default(self, engine):
        with pytest.raises(PermissionError, match="not enabled"):
            engine.check("alice", "read")

    def test_no_op_when_fail_open_and_not_enabled(self, slicer):
        open_engine = CedarEngine(
            namespace="TodoApp",
            principal_type="User",
            schema=TEST_SCHEMA,
            policies=TEST_POLICIES,
            slicer=slicer,
            fail_open_when_unavailable=True,
        )
        open_engine.check("alice", "read")


class TestRequireEnforcement:
    def test_raises_when_not_enabled(self, engine):
        with pytest.raises(RuntimeError, match="Cedar enforcement required"):
            engine.require_enforcement()

    def test_ok_when_enabled(self, engine, fake, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        fake({"ok": True, "warnings": []})
        engine.load()
        engine.require_enforcement()


class TestPerCallContext:
    def test_per_call_context_merges_over_provider(self, engine, fake, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        module = fake({"ok": True, "warnings": []})
        engine.load()
        module.reply = {"decision": "allow"}

        assert engine.is_authorized("alice", "read", context={"repl": True})
        _, args = module.calls[-1]
        assert json.loads(args[4]) == {"source": "test", "repl": True}

    def test_per_call_context_overrides_provider_keys(self, engine, fake, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        module = fake({"ok": True, "warnings": []})
        engine.load()
        module.reply = {"decision": "allow"}

        engine.is_authorized("alice", "read", context={"source": "repl"})
        _, args = module.calls[-1]
        assert json.loads(args[4]) == {"source": "repl"}

    def test_no_per_call_context_keeps_provider(self, engine, fake, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        module = fake({"ok": True, "warnings": []})
        engine.load()
        module.reply = {"decision": "allow"}

        engine.is_authorized("alice", "read")
        _, args = module.calls[-1]
        assert json.loads(args[4]) == {"source": "test"}

    def test_check_passes_context_through(self, engine, fake, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
        module = fake({"ok": True, "warnings": []})
        engine.load()
        module.reply = {"decision": "allow"}

        engine.check("alice", "read", context={"repl": True})
        _, args = module.calls[-1]
        assert json.loads(args[4]) == {"source": "test", "repl": True}
