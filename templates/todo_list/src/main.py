"""Todo List — secure multi-user template (sandboxed REPL + Cedar owner policies)."""

from basilisk import StableBTreeMap, init, post_upgrade, query, text, update
from ic_python_db import Boolean, Database, Entity, ManyToOne, OneToMany, String
from ic_basilisk_toolkit.secure_orm import SecureEntity, setup_secure_orm

__basilisk_features__ = ["browse"]

storage = StableBTreeMap[str, str](
    memory_id=1, max_key_size=100, max_value_size=10000,
)
Database.init(db_storage=storage, audit_enabled=False)


class User(Entity):
    __alias__ = "id"
    id = String(max_length=64)


class TodoList(SecureEntity):
    title = String(max_length=200)
    owner = String(max_length=64)
    items = OneToMany("TodoItem", "todo_list")


class TodoItem(SecureEntity):
    title = String(max_length=500)
    done = Boolean(default=False)
    todo_list = ManyToOne("TodoList", "items")


orm = setup_secure_orm(
    [TodoList, TodoItem],
    namespace="TodoApp",
    principal_type="User",
    principal_entity=User,
)


@init
def init_hook() -> None:
    orm.engine.require_enforcement()


@post_upgrade
def post_upgrade_hook() -> None:
    orm.engine.require_enforcement()


@query
def status() -> text:
    import json

    return json.dumps(orm.status())


@update
def __shell__(code: str) -> text:
    return orm.shell(code)
