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
    RpcError,
    SecureEntity,
    generate_default_policies,
    setup_secure_orm,
)
from ic_python_db import Boolean, Database, ManyToOne, MemoryStorage, OneToMany, String

Database.init(db_storage=MemoryStorage(), audit_enabled=False)


class User(SecureEntity):
    id = String(max_length=64, indexed=True)


class TodoList(SecureEntity):
    title = String(max_length=200)
    owner = String(max_length=64)
    items = OneToMany("TodoItem", "todo_list")


class TodoItem(SecureEntity):
    title = String(max_length=500)
    done = Boolean(default=False)
    todo_list = ManyToOne("TodoList", "items")


class Post(SecureEntity):
    __owner_field__ = "user"

    title = String(max_length=200)
    user = ManyToOne("User", "posts")


User.posts = OneToMany("Post", "user")


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

    def test_owner_field_entity_gets_type_level_list_permit(self):
        schema = _build_schema_dict()
        policies = generate_default_policies(schema, "TodoApp", [TodoList, TodoItem])
        assert (
            'permit (principal, action == TodoApp::Action::"entity.list", '
            'resource == TodoApp::TodoList::"_");'
        ) in policies
        assert "Type-level list permit" in policies

    def test_child_entity_gets_owner_via_parent_permit(self):
        schema = _build_schema_dict()
        policies = generate_default_policies(schema, "TodoApp", [TodoList, TodoItem])
        assert "resource is TodoApp::TodoItem" in policies
        assert (
            "resource has todo_list && resource.todo_list has owner "
            "&& principal has id && resource.todo_list.owner == principal.id"
        ) in policies

    def test_child_entity_gets_type_level_list_permit(self):
        schema = _build_schema_dict()
        policies = generate_default_policies(schema, "TodoApp", [TodoList, TodoItem])
        assert (
            'permit (principal, action == TodoApp::Action::"entity.list", '
            'resource == TodoApp::TodoItem::"_");'
        ) in policies

    def test_type_level_list_permit_only_for_entity_list(self):
        schema = _build_schema_dict()
        policies = generate_default_policies(schema, "TodoApp", [TodoList, TodoItem])
        assert policies.count('action == TodoApp::Action::"entity.list"') == 2
        assert 'resource == TodoApp::TodoList::"_"' in policies
        assert 'resource == TodoApp::TodoItem::"_"' in policies
        assert 'resource is TodoApp::TodoList);' not in policies
        assert 'action == TodoApp::Action::"entity.get"' not in policies
        assert 'action == TodoApp::Action::"entity.update"' not in policies
        assert 'action == TodoApp::Action::"entity.delete"' not in policies

    def test_global_create_permit_present(self):
        schema = _build_schema_dict()
        policies = generate_default_policies(schema, "TodoApp", [TodoList, TodoItem])
        assert 'action == TodoApp::Action::"entity.create"' in policies


class TestActions:
    def test_rpc_action_naming(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        assert orm.actions() == [
            "orm.create",
            "orm.list",
            "orm.get",
            "orm.update",
            "orm.delete",
            "orm.count",
        ]


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

        def fake_is_authorized(principal_id, action, resource_type="", resource_id="", resource_row=None, entities=None, context=None):
            # Upfront type-level list check uses empty id / typed placeholder.
            if action == "entity.list" and resource_id in ("", "_"):
                return True
            return resource_id in allowed_ids and principal_id == "alice"

        monkeypatch.setattr(orm.engine, "is_authorized", fake_is_authorized)

        rows = orm.handle_rpc("alice", "todo_list.list", {})
        assert len(rows) == 1
        assert rows[0]["id"] == first["id"]
        assert second["id"] not in {r["id"] for r in rows}

    def test_list_scalar_filter(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        orm.handle_rpc("alice", "todo_list.create", {"title": "A"})
        orm.handle_rpc("alice", "todo_list.create", {"title": "B"})
        rows = orm.handle_rpc("alice", "todo_list.list", {"title": "A"})
        assert len(rows) == 1
        assert rows[0]["title"] == "A"

    def test_list_unknown_filter_matches_nothing(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        orm.handle_rpc("alice", "todo_list.create", {"title": "A"})
        rows = orm.handle_rpc("alice", "todo_list.list", {"nope": "x"})
        assert rows == []

    def test_list_pagination(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ids = [
            orm.handle_rpc("alice", "todo_list.create", {"title": t})["id"]
            for t in ("A", "B", "C")
        ]
        page = orm.handle_rpc("alice", "todo_list.list", {"from_id": 2, "count": 1})
        assert [r["id"] for r in page] == [ids[1]]
        rest = orm.handle_rpc("alice", "todo_list.list", {"from_id": 2})
        assert [r["id"] for r in rest] == ids[1:]

    def test_list_relation_filter_still_works(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        lst = orm.handle_rpc("alice", "todo_list.create", {"title": "L"})
        orm.handle_rpc("alice", "todo_item.create", {"title": "i1", "todo_list_id": lst["id"]})
        orm.handle_rpc("alice", "todo_item.create", {"title": "i2", "todo_list_id": lst["id"]})
        rows = orm.handle_rpc("alice", "todo_item.list", {"todo_list_id": lst["id"]})
        assert len(rows) == 2
        rows = orm.handle_rpc("alice", "todo_item.list", {"todo_list_id": "999"})
        assert rows == []

    def test_count_returns_visible_row_count(self, fake_cedar, monkeypatch):
        orm = _make_orm(fake_cedar)
        first = orm.handle_rpc("alice", "todo_list.create", {"title": "A"})
        orm.handle_rpc("alice", "todo_list.create", {"title": "B"})

        assert orm.handle_rpc("alice", "todo_list.count", {}) == 2
        assert orm.handle_rpc("alice", "todo_list.count", {"title": "A"}) == 1

        def fake_is_authorized(principal_id, action, resource_type="", resource_id="", resource_row=None, entities=None, context=None):
            return resource_id == first["id"]

        monkeypatch.setattr(orm.engine, "is_authorized", fake_is_authorized)
        assert orm.handle_rpc("alice", "todo_list.count", {}) == 1

    def test_get_missing_returns_none(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        assert orm.handle_rpc("alice", "todo_list.get", {"id": "999"}) is None

    def test_get_existing_denied_raises(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        created = orm.handle_rpc("alice", "todo_list.create", {"title": "A"})
        fake_cedar.reply = {"decision": "deny"}
        with pytest.raises(PermissionError, match="denied"):
            orm.handle_rpc("bob", "todo_list.get", {"id": created["id"]})


class TestStubSource:
    def test_stub_source_contains_expected_parts(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        src = orm.stub_source()
        assert "class TodoList(_Stub)" in src
        assert "class TodoItem(_Stub)" in src
        assert "def eval_repl(code):" in src
        assert 'rpc("orm.update", _entity=type(self).__name__' in src
        assert "def items(self):" in src
        assert 'rpc("orm.list", _entity="TodoItem", todo_list_id=self.id)' in src

    def test_stub_source_has_native_api(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        src = orm.stub_source()
        assert "def instances(cls):" in src
        assert "def mine(cls):" in src
        assert "def count(cls):" in src
        assert "def find(cls, d):" in src
        assert "def load_some(cls, from_id=1, count=50):" in src
        assert "def load(cls, id):" in src

    def test_stub_source_is_deterministic(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        assert orm.stub_source() == orm.stub_source()


class TestStubBehavior:
    def _exec_stub(self, orm, rpc_calls: List[tuple]):
        calls: List[tuple] = []

        def rpc(action, **kwargs):
            calls.append((action, kwargs))
            entity = kwargs.get("_entity", "")
            if action == "orm.list" and entity == "TodoList":
                return [{"id": "1", "title": "T", "owner": "alice"}]
            if action == "orm.count" and entity == "TodoList":
                return 1
            if action == "orm.create" and entity == "TodoList":
                return {"id": "2", "title": kwargs.get("title", ""), "owner": "alice"}
            if action == "orm.update" and entity == "TodoList":
                return {"id": kwargs["id"], "title": kwargs.get("title", "T"), "owner": "alice"}
            if action == "orm.get" and entity == "TodoList":
                if kwargs["id"] == "999":
                    return None
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
        assert ("orm.update", {"_entity": "TodoList", "id": "1", "title": "x"}) in calls

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
        assert not any(c[0] == "orm.update" for c in calls)

    def test_create_and_mine_wrap_dicts(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, _ = self._exec_stub(orm, [])
        created = ns["TodoList"].create(title="New")
        assert created.title == "New"
        assert created.id == "2"
        mine = ns["TodoList"].mine()
        assert len(mine) == 1
        assert mine[0].id == "1"

    def test_instances_matches_mine(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, calls = self._exec_stub(orm, [])
        rows = ns["TodoList"].instances()
        assert len(rows) == 1
        assert rows[0].title == "T"
        assert ("orm.list", {"_entity": "TodoList"}) in calls

    def test_count_returns_int(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, calls = self._exec_stub(orm, [])
        assert ns["TodoList"].count() == 1
        assert ("orm.count", {"_entity": "TodoList"}) in calls

    def test_find_passes_filter_dict(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, calls = self._exec_stub(orm, [])
        rows = ns["TodoList"].find({"title": "T"})
        assert ("orm.list", {"_entity": "TodoList", "title": "T"}) in calls
        assert len(rows) == 1

    def test_find_translates_stub_relations(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, calls = self._exec_stub(orm, [])
        lst = ns["TodoList"]._wrap({"id": "7", "title": "A", "owner": "alice"})
        ns["TodoItem"].find({"todo_list": lst})
        assert ("orm.list", {"_entity": "TodoItem", "todo_list_id": "7"}) in calls

    def test_load_some_passes_pagination(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, calls = self._exec_stub(orm, [])
        ns["TodoList"].load_some(from_id=5, count=10)
        assert ("orm.list", {"_entity": "TodoList", "from_id": 5, "count": 10}) in calls

    def test_load_missing_returns_none(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, _ = self._exec_stub(orm, [])
        assert ns["TodoList"].load("999") is None
        assert ns["TodoList"].load("1").id == "1"

    def test_constructor_creates(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, calls = self._exec_stub(orm, [])
        lst = ns["TodoList"](title="New")
        assert lst.title == "New"
        assert lst.id == "2"
        assert ("orm.create", {"_entity": "TodoList", "title": "New"}) in calls

    def test_class_getitem_loads(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, calls = self._exec_stub(orm, [])
        lst = ns["TodoList"]["1"]
        assert lst.id == "1"
        assert lst.title == "T"
        assert ("orm.get", {"_entity": "TodoList", "id": "1"}) in calls

    def test_class_getitem_missing_returns_none(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, _ = self._exec_stub(orm, [])
        assert ns["TodoList"]["999"] is None

    def test_repl_namespace_persists(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, _ = self._exec_stub(orm, [])
        eval_repl = ns["eval_repl"]
        eval_repl("a = 5")
        out = eval_repl("print(a)")
        assert out == "5\n"

    def test_repl_last_expression_displayed(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, calls = self._exec_stub(orm, [])
        out = ns["eval_repl"]("TodoList.create(title='New')")
        assert "TodoList(" in out
        assert ("orm.create", {"_entity": "TodoList", "title": "New"}) in calls

    def test_repl_assignment_no_extra_display(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        ns, _ = self._exec_stub(orm, [])
        out = ns["eval_repl"]("a = 5")
        assert out.strip() == ""


class TestReplErrors:
    def test_format_sandbox_error_strips_nesting(self):
        from ic_basilisk_toolkit.secure_orm import _format_sandbox_error

        raw = (
            "sandboxed call raised: PermissionError: "
            "'entity.get' denied by policy"
        )
        out = _format_sandbox_error(raw)
        assert out.startswith("✗")
        assert "access denied" in out.lower()

    def test_rpc_deny_message_includes_entity_and_id(self):
        from ic_basilisk_toolkit.secure_orm import _rpc_deny_message

        msg = _rpc_deny_message(
            "todo_list.get",
            {"id": "1"},
            PermissionError("'entity.get' denied by policy"),
        )
        assert "TodoList/1" in msg
        assert "access denied" in msg


class TestCedarIntrospection:
    def test_cedar_policies_action(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        import json

        out = json.loads(orm.cedar('{"action": "policies"}'))
        assert "base_policies" in out
        assert "extra_policies" in out
        assert "policies" in out
        assert "TodoApp::TodoList" in out["policies"] or "entity.create" in out["policies"]

    def test_cedar_snapshot_default(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        import json

        out = json.loads(orm.cedar(""))
        assert out["namespace"] == "TodoApp"
        assert "schema" in out
        assert "policies" in out

    def test_cedar_unknown_action(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        import json

        out = json.loads(orm.cedar('{"action": "nope"}'))
        assert "error" in out

    def test_reload_policies_updates_extra(self, fake_cedar):
        orm = _make_orm(fake_cedar)
        extra = '// custom\npermit(principal, action, resource);'
        out = orm.reload_policies(extra)
        assert out["ok"] is True
        assert out["extra_policies"] == extra
        assert extra in out["policies"]


class TestModuleImport:
    def test_import_without_native_modules(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "ic_basilisk_toolkit.secure_orm", raising=False)
        monkeypatch.setitem(sys.modules, "ic_basilisk_toolkit.cedar_engine", None)
        # Re-import path: secure_orm uses try/except at import time; verify symbols exist.
        from ic_basilisk_toolkit import secure_orm as mod

        assert mod.SecureEntity is not None
        assert mod.RpcError is not None


class TestSetupSchemaPassThrough:
    def test_actions_and_context_passed_to_schema(self, fake_cedar):
        orm = setup_secure_orm(
            [TodoList, TodoItem],
            "TodoApp",
            principal_entity=User,
            actions={"read": "read", "write": "write", "entity.create": "write",
                     "entity.get": "read", "entity.list": "read",
                     "entity.update": "write", "entity.delete": "write"},
            context={"extension": "String", "repl": "Bool"},
        )
        schema = orm.engine.schema
        assert "action read\n" in schema
        assert 'action "entity.create" in [write]' in schema
        assert "extension?: String" in schema
        assert "repl?: Bool" in schema
        # Self-grouped actions are declared once, not duplicated.
        assert schema.count("action read") == 1

    def test_memberships_passed_to_schema(self, fake_cedar):
        orm = setup_secure_orm(
            [TodoList, TodoItem],
            "TodoApp",
            principal_entity=User,
            memberships={"TodoItem": ["todo_list"]},
        )
        assert "entity TodoItem in [TodoList]" in orm.engine.schema

    def test_shell_context_stored(self, fake_cedar):
        orm = setup_secure_orm(
            [TodoList, TodoItem],
            "TodoApp",
            principal_entity=User,
            shell_context={"repl": True},
        )
        assert orm._shell_context == {"repl": True}

    def test_shell_context_default_none(self, fake_cedar):
        orm = setup_secure_orm([TodoList, TodoItem], "TodoApp", principal_entity=User)
        assert orm._shell_context is None


class TestRelationOwnerOnCreate:
    def _make_post_orm(self, fake_cedar):
        alice = User(id="alice")
        bob = User(id="bob")
        orm = setup_secure_orm(
            [Post],
            "TodoApp",
            principal_type="User",
            principal_entity=User,
        )
        return orm, alice, bob

    def test_create_forces_owner_relation_to_principal(self, fake_cedar):
        orm, alice, bob = self._make_post_orm(fake_cedar)
        row = orm.handle_rpc(
            alice._id,
            "post.create",
            {"title": "Hello", "user_id": bob._id},
        )
        assert row["user_id"] == alice._id
        loaded = Post.load(row["id"])
        assert loaded.user._id == alice._id

    def test_create_raises_when_principal_missing(self, fake_cedar):
        orm, _, _ = self._make_post_orm(fake_cedar)
        with pytest.raises(RpcError, match="User eve not found"):
            orm.handle_rpc("eve", "post.create", {"title": "Ghost"})

    def test_create_without_principal_entity_unchanged(self, fake_cedar):
        alice = User(id="alice")
        bob = User(id="bob")
        orm = setup_secure_orm([User, Post], "TodoApp", principal_type="User")
        row = orm.handle_rpc(
            alice._id,
            "post.create",
            {"title": "Hello", "user_id": bob._id},
        )
        assert row["user_id"] == bob._id
        loaded = Post.load(row["id"])
        assert loaded.user._id == bob._id


class TestBasiliskSandboxFallback:
    """_ensure_basilisk_sandbox registers the vendored host module only when
    the real basilisk.sandbox is missing."""

    def test_registers_vendored_module_when_missing(self, monkeypatch):
        import types

        from ic_basilisk_toolkit import basilisk_sandbox_host
        from ic_basilisk_toolkit.secure_orm import _ensure_basilisk_sandbox

        fake_basilisk = types.ModuleType("basilisk")
        fake_basilisk.__path__ = []
        monkeypatch.setitem(sys.modules, "basilisk", fake_basilisk)
        # sys.modules entry of None forces ImportError on import.
        monkeypatch.setitem(sys.modules, "basilisk.sandbox", None)

        _ensure_basilisk_sandbox()

        assert sys.modules["basilisk.sandbox"] is basilisk_sandbox_host
        from basilisk.sandbox import spawn_sandboxed  # noqa: F401

    def test_never_overrides_real_module(self, monkeypatch):
        import types

        from ic_basilisk_toolkit.secure_orm import _ensure_basilisk_sandbox

        real = types.ModuleType("basilisk.sandbox")
        monkeypatch.setitem(sys.modules, "basilisk.sandbox", real)

        _ensure_basilisk_sandbox()

        assert sys.modules["basilisk.sandbox"] is real
