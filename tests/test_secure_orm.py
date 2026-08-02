"""Unit tests for ic_basilisk_toolkit.secure_orm.

Client-side: mock _basilisk_cedar via cedar._module and use in-memory ic-python-db.
Run: pytest tests/test_secure_orm.py -v
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ic_basilisk_toolkit import cedar
from ic_basilisk_toolkit.secure_orm import (
    SecureEntity,
    generate_default_policies,
    setup_secure_orm,
)
from ic_python_db import Boolean, Database, ManyToOne, MemoryStorage, OneToMany, String

Database.init(db_storage=MemoryStorage(), audit_enabled=False)


class User(SecureEntity):
    id = String(max_length=64)


class TodoList(SecureEntity):
    title = String(max_length=200)
    owner = String(max_length=64)
    items = OneToMany("TodoItem", "todo_list")


class TodoItem(SecureEntity):
    title = String(max_length=500)
    done = Boolean(default=False)
    todo_list = ManyToOne("TodoList", "items")


class FakeModule:
    """Stands in for _basilisk_cedar."""

    def __init__(self, reply=None):
        self.reply = reply or {"decision": "allow", "ok": True, "warnings": []}
        self.calls: List[tuple] = []

    def _respond(self, name, args):
        self.calls.append((name, args))
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)

    def load(self, *args):
        return self._respond("load", args)

    def is_authorized(self, *args):
        return self._respond("is_authorized", args)

    def authorize_many(self, *args):
        return self._respond("authorize_many", args)


@pytest.fixture(autouse=True)
def clean_db():
    Database.get_instance().clear()
    yield
    Database.get_instance().clear()


@pytest.fixture
def fake_cedar(monkeypatch):
    module = FakeModule()
    monkeypatch.setitem(sys.modules, "_basilisk_cedar", object())
    monkeypatch.setattr(cedar, "_module", lambda: module)
    return module


def _build_schema_dict():
    from ic_python_db.schema import build_schema

    return build_schema(
        {
            "User": User,
            "TodoList": TodoList,
            "TodoItem": TodoItem,
        }
    )


def _make_orm(fake_cedar, *, deny=False):
    if deny:
        fake_cedar.reply = {"decision": "deny"}
    orm = setup_secure_orm(
        [TodoList, TodoItem],
        "TodoApp",
        principal_type="User",
        principal_entity=User,
    )
    return orm


class TestPolicyGeneration:
    def test_owner_field_entity_gets_owner_permit(self):
        schema = _build_schema_dict()
        policies = generate_default_policies(schema, "TodoApp", [TodoList, TodoItem])
        assert "resource is TodoApp::TodoList" in policies
        assert "resource has owner && principal has id && resource.owner == principal.id" in policies

    def test_child_entity_gets_owner_via_parent_permit(self):
        schema = _build_schema_dict()
        policies = generate_default_policies(schema, "TodoApp", [TodoList, TodoItem])
        assert "resource is TodoApp::TodoItem" in policies
        assert (
            "resource has todo_list && resource.todo_list has owner "
            "&& principal has id && resource.todo_list.owner == principal.id"
        ) in policies

    def test_global_create_permit_present(self):
        schema = _build_schema_dict()
        policies = generate_default_policies(schema, "TodoApp", [TodoList, TodoItem])
        assert 'action == TodoApp::Action::"entity.create"' in policies


class TestActions:
    def test_rpc_action_naming(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        assert "todo_list.create" in orm.actions()
        assert "todo_list.list" in orm.actions()
        assert "todo_item.update" in orm.actions()


class TestHandleRpc:
    def test_create_forces_owner(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        row = orm.handle_rpc(
            "alice",
            "todo_list.create",
            {"title": "Mine", "owner": "eve"},
        )
        assert row["owner"] == "alice"
        loaded = TodoList.load(row["id"])
        assert loaded.owner == "alice"

    def test_create_rejects_id_kwarg(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        row = orm.handle_rpc(
            "alice",
            "todo_list.create",
            {"title": "X", "id": "999"},
        )
        assert row["id"] != "999"

    def test_update_rejects_owner_and_id(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        created = orm.handle_rpc("alice", "todo_list.create", {"title": "A"})
        updated = orm.handle_rpc(
            "alice",
            "todo_list.update",
            {"id": created["id"], "title": "B", "owner": "eve"},
        )
        assert updated["title"] == "B"
        assert updated["owner"] == "alice"
        assert updated["id"] == created["id"]

    def test_delete_calls_row_delete(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        created = orm.handle_rpc("alice", "todo_list.create", {"title": "Go"})
        orm.handle_rpc("alice", "todo_list.delete", {"id": created["id"]})
        assert TodoList.load(created["id"]) is None

    def test_cross_user_update_denied(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        created = orm.handle_rpc("alice", "todo_list.create", {"title": "A"})
        fake_cedar.reply = {"decision": "deny"}
        with pytest.raises(PermissionError, match="denied"):
            orm.handle_rpc("bob", "todo_list.update", {"id": created["id"], "title": "X"})

    def test_list_filters_denied_rows(self, fake_cedar, monkeypatch):
        orm = _make_orm(fake_cedar)
        first = orm.handle_rpc("alice", "todo_list.create", {"title": "A"})
        second = orm.handle_rpc("bob", "todo_list.create", {"title": "B"})

        allowed_ids = {first["id"]}

        def fake_is_authorized(principal_id, action, resource_type="", resource_id="", resource_row=None, entities=None):
            return resource_id in allowed_ids and principal_id == "alice"

        monkeypatch.setattr(orm.engine, "is_authorized", fake_is_authorized)

        rows = orm.handle_rpc("alice", "todo_list.list", {})
        assert len(rows) == 1
        assert rows[0]["id"] == first["id"]
        assert second["id"] not in {r["id"] for r in rows}


class TestStubSource:
    def test_stub_source_contains_expected_parts(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        src = orm.stub_source()
        assert "class TodoList(_Stub)" in src
        assert "class TodoItem(_Stub)" in src
        assert "def eval_repl(code):" in src
        assert 'rpc(self._prefix + ".update"' in src
        assert "def items(self):" in src
        assert 'rpc("todo_item.list", todo_list_id=self.id)' in src

    def test_stub_source_is_deterministic(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        assert orm.stub_source() == orm.stub_source()


class TestStubBehavior:
    def _exec_stub(self, orm, rpc_calls: List[tuple]):
        calls: List[tuple] = []

        def rpc(action, **kwargs):
            calls.append((action, kwargs))
            if action == "todo_list.list":
                return [{"id": "1", "title": "T", "owner": "alice"}]
            if action == "todo_list.create":
                return {"id": "2", "title": kwargs.get("title", ""), "owner": "alice"}
            if action == "todo_list.update":
                return {"id": kwargs["id"], "title": kwargs.get("title", "T"), "owner": "alice"}
            if action == "todo_list.get":
                return {"id": kwargs["id"], "title": "T", "owner": "alice"}
            return {}

        ns: Dict[str, Any] = {"rpc": rpc, "__builtins__": __builtins__}
        exec(compile(orm.stub_source(), "<stub>", "exec"), ns, ns)
        return ns, calls

    def test_setattr_triggers_update_rpc(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, calls = self._exec_stub(orm, [])
        lst = ns["TodoList"]._wrap({"id": "1", "title": "A", "owner": "alice"})
        lst.title = "x"
        assert ("todo_list.update", {"id": "1", "title": "x"}) in calls

    def test_wrap_does_not_rpc(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, calls = self._exec_stub(orm, [])
        before = len(calls)
        ns["TodoList"]._wrap({"id": "1", "title": "A", "owner": "alice"})
        assert len(calls) == before

    def test_underscore_and_id_do_not_rpc(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, calls = self._exec_stub(orm, [])
        lst = ns["TodoList"]._wrap({"id": "1", "title": "A", "owner": "alice"})
        lst._x = 1
        assert not any(c[0] == "todo_list.update" for c in calls)

    def test_create_and_mine_wrap_dicts(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, _ = self._exec_stub(orm, [])
        created = ns["TodoList"].create(title="New")
        assert created.title == "New"
        assert created.id == "2"
        mine = ns["TodoList"].mine()
        assert len(mine) == 1
        assert mine[0].id == "1"


class TestModuleImport:
    def test_import_without_native_modules(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "ic_basilisk_toolkit.secure_orm", raising=False)
        monkeypatch.setitem(sys.modules, "ic_basilisk_toolkit.cedar_engine", None)
        # Re-import path: secure_orm uses try/except at import time; verify symbols exist.
        from ic_basilisk_toolkit import secure_orm as mod

        assert mod.SecureEntity is not None
        assert mod.RpcError is not None
