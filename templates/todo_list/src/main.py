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
    public = Boolean(default=False)
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
    extra_policies="",  # owner-only at boot; enable via enable_public_read()
)


def _caller_owns_a_list(caller: str) -> bool:
    for row in TodoList.instances():
        if getattr(row, "owner", None) == caller:
            return True
    return False


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


@query
def __cedar__(query: str) -> text:
    """Read-only Cedar introspection (schema, policies, enforcement status)."""
    return orm.cedar(query)


@update
def enable_public_read(enabled: bool) -> text:
    """Load or remove runtime public-read Cedar policies (demo: list owners only)."""
    import json

    from basilisk import ic

    from cedar_extra import PUBLIC_READ

    caller = str(ic.caller())
    if not _caller_owns_a_list(caller):
        return json.dumps(
            {"ok": False, "error": "only list owners may toggle public read (demo guard)"}
        )
    out = orm.reload_policies(PUBLIC_READ if enabled else "")
    return json.dumps(out)


@update
def __shell__(code: str) -> text:
    return orm.shell(code)
