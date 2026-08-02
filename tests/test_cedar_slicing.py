"""Unit tests for ic_basilisk_toolkit.cedar_slicing.

Run: pytest tests/test_cedar_slicing.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ic_basilisk_toolkit.cedar_slicing import NEVER_PROJECT, Slicer

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


@pytest.fixture
def slicer():
    return Slicer("TodoApp", TEST_SCHEMA)


class TestUid:
    def test_uid_text_format(self, slicer):
        assert slicer.uid("User", "alice") == 'TodoApp::User::"alice"'

    def test_uid_json_format(self, slicer):
        assert slicer.uid_json("TodoItem", "1") == {
            "type": "TodoApp::TodoItem",
            "id": "1",
        }


class TestSchemaParsing:
    def test_declared_types(self, slicer):
        assert slicer.declared_types() == frozenset(
            {"TodoItem", "TodoList", "User"}
        )

    def test_declared_attrs_for_todo_item(self, slicer):
        assert slicer.declared_attrs("TodoItem") == frozenset(
            {"done", "title", "todo_list"}
        )

    def test_declared_attrs_unknown_type_is_none(self, slicer):
        assert slicer.declared_attrs("Ghost") is None

    def test_declared_types_cached(self, slicer):
        first = slicer.declared_types()
        slicer.schema = "entity Broken {"
        assert slicer.declared_types() is first


class TodoList:
    def __init__(self, list_id, owner="", title=""):
        self._id = list_id
        self.owner = owner
        self.title = title


class TodoItem:
    def __init__(self, item_id, done=False, title="", todo_list=None):
        self._id = item_id
        self.done = done
        self.title = title
        self.todo_list = todo_list
        self._private = "hidden"
        self.password = "secret"
        self.rating = 4.5
        self.tags = ["a", "b"]
        self.ghost = "undeclared"


class TestProjection:
    def test_scalar_projection(self, slicer):
        row = TodoItem("1", done=True, title="Buy milk")
        entities = slicer.resource_entity("TodoItem", "1", row)
        assert len(entities) == 1
        assert entities[0]["attrs"] == {
            "done": True,
            "title": "Buy milk",
        }

    def test_skips_underscore_never_project_float_list(self, slicer):
        row = TodoItem("1")
        attrs = slicer._attrs(row, slicer.declared_attrs("TodoItem"))
        assert "_private" not in attrs
        assert "password" not in attrs
        assert "rating" not in attrs
        assert "tags" not in attrs
        assert "ghost" not in attrs

    def test_never_project_constant(self):
        assert "password" in NEVER_PROJECT

    def test_relation_becomes_entity_ref(self, slicer):
        parent = TodoList("list-1", owner="alice", title="Chores")
        row = TodoItem("1", title="Task", todo_list=parent)
        entities = slicer.resource_entity("TodoItem", "1", row)
        assert entities[0]["attrs"]["todo_list"] == {
            "__entity": {"type": "TodoApp::TodoList", "id": "list-1"},
        }

    def test_relation_accepts_id_not_underscore_id(self, slicer):
        class TodoListAlt:
            def __init__(self):
                self.id = "list-2"
                self.owner = "bob"

        TodoListAlt.__name__ = "TodoList"
        row = TodoItem("2", todo_list=TodoListAlt())
        entities = slicer.resource_entity("TodoItem", "2", row)
        assert entities[0]["attrs"]["todo_list"]["__entity"]["id"] == "list-2"


class TestRowEntityDepth:
    def test_depth_one_includes_related_entity(self, slicer):
        parent = TodoList("list-1", owner="alice", title="Chores")
        row = TodoItem("1", title="Task", todo_list=parent)
        entities = slicer.row_entity("TodoItem", "1", row, depth=1)
        uids = {(e["uid"]["type"], e["uid"]["id"]) for e in entities}
        assert ("TodoApp::TodoItem", "1") in uids
        assert ("TodoApp::TodoList", "list-1") in uids
        list_entity = next(
            e for e in entities if e["uid"]["id"] == "list-1"
        )
        assert list_entity["attrs"] == {"owner": "alice", "title": "Chores"}

    def test_depth_zero_skips_related_entity(self, slicer):
        parent = TodoList("list-1", owner="alice")
        row = TodoItem("1", todo_list=parent)
        entities = slicer.row_entity("TodoItem", "1", row, depth=0)
        assert len(entities) == 1
        assert entities[0]["attrs"]["todo_list"]["__entity"]["id"] == "list-1"


class TestResourceEntity:
    def test_empty_id_returns_nothing(self, slicer):
        assert slicer.resource_entity("TodoItem", "", TodoItem("1")) == []

    def test_undeclared_type_returns_nothing(self, slicer):
        assert slicer.resource_entity("Ghost", "1", object()) == []


class TestPrincipalEntity:
    def test_unknown_principal_still_yields_entity_with_id(self, slicer):
        entities = slicer.principal_entity("alice")
        assert len(entities) == 1
        user = entities[0]
        assert user["uid"] == {"type": "TodoApp::User", "id": "alice"}
        assert user["attrs"] == {"id": "alice"}
        assert user["parents"] == []

    def test_parents_added_as_entities_and_parent_links(self, slicer):
        parent = slicer.uid_json("TodoList", "admins")
        entities = slicer.principal_entity("alice", parents=[parent])
        assert len(entities) == 2
        user = next(e for e in entities if e["uid"]["id"] == "alice")
        assert user["parents"] == [parent]
        group = next(e for e in entities if e["uid"]["id"] == "admins")
        assert group["uid"]["type"] == "TodoApp::TodoList"


class TestSliceFor:
    def test_combines_principal_and_resource(self, slicer):
        row = TodoItem("1", title="Task")
        entities = slicer.slice_for("alice", "TodoItem", "1", row)
        types = {e["uid"]["type"] for e in entities}
        assert "TodoApp::User" in types
        assert "TodoApp::TodoItem" in types


class TestPrincipalRowProjection:
    def test_principal_row_attrs_projected_and_id_forced(self):
        from ic_basilisk_toolkit.cedar_slicing import Slicer

        schema = """
namespace TodoApp {
    entity User {
        id?: String,
        nickname?: String,
    };
    action read appliesTo { principal: [User], resource: [User] };
}
"""

        class Row:
            id = "internal-db-id"
            nickname = "Al"
            password = "hunter2"

        entities = Slicer("TodoApp", schema).principal_entity("alice", row=Row())
        user = entities[-1]
        assert user["uid"] == {"type": "TodoApp::User", "id": "alice"}
        assert user["attrs"]["nickname"] == "Al"
        assert user["attrs"]["id"] == "alice"
        assert "password" not in user["attrs"]

    def test_slice_for_passes_principal_row(self):
        from ic_basilisk_toolkit.cedar_slicing import Slicer

        schema = """
namespace TodoApp {
    entity User {
        id?: String,
        nickname?: String,
    };
    action read appliesTo { principal: [User], resource: [User] };
"""

        class Row:
            nickname = "Al"

        entities = Slicer("TodoApp", schema).slice_for("alice", principal_row=Row())
        user = [e for e in entities if e["uid"]["type"] == "TodoApp::User"][0]
        assert user["attrs"]["nickname"] == "Al"
