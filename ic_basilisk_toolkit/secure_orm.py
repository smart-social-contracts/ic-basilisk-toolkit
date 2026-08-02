"""SecureEntity — sandboxed ORM with Cedar owner policies.

Combines ic-python-db entities, Cedar authorization, and a sandboxed REPL whose
mutations cross the trust boundary only through typed RPC actions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from ic_python_db import Entity
from ic_python_db.schema import build_schema

from ic_basilisk_toolkit.cedar_schema import generate_cedar_schema

try:
    from ic_basilisk_toolkit.cedar_engine import CedarEngine
except ImportError:  # pragma: no cover - client-side / partial installs
    CedarEngine = None  # type: ignore[misc, assignment]

try:
    from ic_basilisk_toolkit.cedar_slicing import Slicer
except ImportError:  # pragma: no cover
    Slicer = None  # type: ignore[misc, assignment]

_CRUD = ("create", "list", "get", "update", "delete", "count")
_ORM_RPC_PREFIX = "orm"


def _orm_rpc(op: str) -> str:
    return f"{_ORM_RPC_PREFIX}.{op}"


def _ensure_basilisk_sandbox() -> None:
    """Make ``basilisk.sandbox`` importable when the build omits it.

    Some Basilisk canister builds do not bundle ``basilisk/sandbox.py`` (it
    lives in ``compiler/custom_modules``). Fall back to the vendored
    host-side copy so ``SecureORM.shell()`` works out of the box. A real
    ``basilisk.sandbox`` is never overridden.
    """
    import sys
    import types

    try:
        import basilisk.sandbox  # noqa: F401

        return
    except ImportError:
        pass

    from ic_basilisk_toolkit import basilisk_sandbox_host

    bas = sys.modules.get("basilisk")
    if bas is None:
        bas = types.ModuleType("basilisk")
        sys.modules["basilisk"] = bas
    if not getattr(bas, "__path__", None):
        bas.__path__ = []
    sys.modules["basilisk.sandbox"] = basilisk_sandbox_host


def _format_sandbox_error(message: str) -> str:
    """Flatten nested sandbox/RPC errors for REPL display."""
    msg = message.strip()
    for prefix in (
        "sandboxed call raised: ",
        "RuntimeError: rpc failed: ",
        "PermissionError: ",
    ):
        if msg.startswith(prefix):
            msg = msg[len(prefix) :].strip()
    if "denied by policy" in msg and not msg.lower().startswith("access denied"):
        msg = msg.replace("' denied by policy", " denied").replace("denied by policy", "denied")
        if "entity." in msg:
            action = msg.strip("'")
            msg = f"access denied: {action} (not authorized for your principal)"
    if msg.lower().startswith("access denied") or " denied" in msg:
        return f"✗ {msg}"
    return msg


def _rpc_deny_message(action: str, kwargs: dict, exc: PermissionError) -> str:
    """Human-readable Cedar denial for sandbox RPC failures."""
    if "." in action:
        entity_key, op = action.rsplit(".", 1)
    else:
        entity_key, op = action, "action"
    row_id = kwargs.get("id", "")
    entity_label = entity_key.replace("_", " ").title().replace(" ", "")
    if row_id:
        return (
            f"access denied: {op} on {entity_label}/{row_id} "
            f"(not authorized for your principal)"
        )
    return f"access denied: {op} on {entity_label} ({exc})"
_SCALAR_TYPES = frozenset({"String", "Integer", "Boolean"})


class RpcError(Exception):
    """Plain host-side RPC failure (missing row, unknown action, etc.)."""


class SecureEntity(Entity):
    """Entity base class with Cedar owner-policy metadata."""

    __owner_field__ = "owner"
    __default_policy__ = "owner_only"


def _snake_case(name: str) -> str:
    # Regex-free: the wasm CPython build ships a partial `re` module.
    out: List[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            prev = name[i - 1]
            nxt = name[i + 1] if i + 1 < len(name) else ""
            if prev.islower() or prev.isdigit() or (prev.isupper() and nxt.islower()):
                out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _relation_targets(descriptor: Dict[str, Any]) -> List[str]:
    target = descriptor.get("target")
    if target is None:
        return []
    return list(target) if isinstance(target, list) else [target]


def generate_default_policies(
    schema: Dict[str, Any],
    namespace: str,
    entities: List[Type[SecureEntity]],
    principal_type: str = "User",
) -> str:
    """Render default owner and create permits for *entities*."""
    by_name = {cls.__name__: cls for cls in entities}
    lines: List[str] = []

    for entity_name in sorted(schema):
        if entity_name == principal_type:
            continue
        descriptor = schema[entity_name]
        fields = descriptor.get("fields", {})
        relationships = descriptor.get("relationships", {})
        cls = by_name.get(entity_name)
        owner_field = getattr(cls, "__owner_field__", "owner") if cls else "owner"

        if (
            owner_field in fields
            and fields[owner_field].get("type") == "String"
        ):
            lines.append(
                f"// Type-level list permit: upfront _list checks use typed placeholder "
                f'Ns::Type::"_" (no row attrs); row filtering still applies per row.\n'
                f"permit (principal, action == {namespace}::Action::\"entity.list\", "
                f'resource == {namespace}::{entity_name}::"_");'
            )
            lines.append(
                f"permit (principal, action, resource is {namespace}::{entity_name})\n"
                f"when {{ resource has {owner_field} && principal has id "
                f"&& resource.{owner_field} == principal.id }};"
            )
            continue

        for rel_name in sorted(relationships):
            rel = relationships[rel_name]
            if rel.get("type") != "ManyToOne":
                continue
            targets = _relation_targets(rel)
            if len(targets) != 1:
                continue
            target = targets[0]
            target_fields = schema.get(target, {}).get("fields", {})
            target_cls = by_name.get(target)
            target_owner = (
                getattr(target_cls, "__owner_field__", "owner")
                if target_cls
                else "owner"
            )
            if (
                target_owner in target_fields
                and target_fields[target_owner].get("type") == "String"
            ):
                lines.append(
                    f"// Type-level list permit: upfront _list checks use typed placeholder "
                    f'Ns::Type::"_" (no row attrs); row filtering still applies per row.\n'
                    f"permit (principal, action == {namespace}::Action::\"entity.list\", "
                    f'resource == {namespace}::{entity_name}::"_");'
                )
                lines.append(
                    f"permit (principal, action, resource is {namespace}::{entity_name})\n"
                    f"when {{ resource has {rel_name} && resource.{rel_name} has "
                    f"{target_owner} && principal has id "
                    f"&& resource.{rel_name}.{target_owner} == principal.id }};"
                )
                break

    lines.append(
        f'permit (principal, action == {namespace}::Action::"entity.create", resource);'
    )
    return "\n\n".join(lines)


def _generate_stub_source(
    entities: List[Type[SecureEntity]], schema: Dict[str, Any]
) -> str:
    """Deterministic sandbox stub module (string templating, no imports)."""
    sorted_entities = sorted(entities, key=lambda c: c.__name__)
    orm_create = _orm_rpc("create")
    orm_list = _orm_rpc("list")
    orm_get = _orm_rpc("get")
    orm_update = _orm_rpc("update")
    orm_delete = _orm_rpc("delete")
    orm_count = _orm_rpc("count")
    class_blocks: List[str] = []

    for cls in sorted_entities:
        name = cls.__name__
        prefix = _snake_case(name)
        descriptor = schema.get(name, {})
        relationships = descriptor.get("relationships", {})

        methods = [
            f'    _prefix = "{prefix}"',
            "",
            "    @classmethod",
            "    def _wrap(cls, d):",
            '        obj = cls.__new__(cls)',
            '        object.__setattr__(obj, "_data", dict(d))',
            "        return obj",
            "",
            "    @classmethod",
            "    def create(cls, **kw):",
            f'        return cls._wrap(rpc("{orm_create}", _entity="{name}", **_Stub._rpc_kwargs(kw)))',
            "",
            "    @classmethod",
            "    def instances(cls):",
            "        # Cedar-filtered: only rows the caller may list.",
            f'        return [cls._wrap(d) for d in rpc("{orm_list}", _entity="{name}")]',
            "",
            "    @classmethod",
            "    def mine(cls):",
            "        return cls.instances()",
            "",
            "    @classmethod",
            "    def count(cls):",
            "        # Caller-scoped row count (int; rows are not transferred).",
            f'        return rpc("{orm_count}", _entity="{name}")',
            "",
            "    @classmethod",
            "    def find(cls, d):",
            f'        return [cls._wrap(x) for x in rpc("{orm_list}", _entity="{name}", **_Stub._rpc_kwargs(d))]',
            "",
            "    @classmethod",
            "    def load_some(cls, from_id=1, count=50):",
            f'        rows = rpc("{orm_list}", _entity="{name}", from_id=from_id, count=count)',
            "        return [cls._wrap(x) for x in rows]",
            "",
            "    @classmethod",
            "    def get(cls, id):",
            f'        d = rpc("{orm_get}", _entity="{name}", id=id)',
            "        return cls._wrap(d) if d else None",
            "",
            "    @classmethod",
            "    def load(cls, id):",
            "        return cls.get(id)",
            "",
            "    @classmethod",
            "    def list(cls):",
            "        return cls.instances()",
        ]

        for rel_name in sorted(relationships):
            rel = relationships[rel_name]
            if rel.get("type") != "OneToMany":
                continue
            targets = _relation_targets(rel)
            if len(targets) != 1:
                continue
            child_name = targets[0]
            child_prefix = _snake_case(child_name)
            filter_kw = f"{rel_name}_id"
            child_rels = schema.get(child_name, {}).get("relationships", {})
            for child_rel, child_desc in sorted(child_rels.items()):
                if (
                    child_desc.get("type") == "ManyToOne"
                    and _relation_targets(child_desc) == [name]
                ):
                    filter_kw = f"{child_rel}_id"
                    break
            methods.extend(
                [
                    "",
                    f"    def {rel_name}(self):",
                    f'        rows = rpc("{orm_list}", _entity="{child_name}", {filter_kw}=self.id)',
                    f"        return [{child_name}._wrap(d) for d in rows]",
                ]
            )

        class_blocks.append(f"class {name}(_Stub):\n" + "\n".join(methods))

    entity_names = ", ".join(f'"{cls.__name__}"' for cls in sorted_entities)
    classes_section = "\n\n".join(class_blocks)
    rebuild_lines = "\n".join(
        f'{cls.__name__} = _make_entity_class("{cls.__name__}", "{_snake_case(cls.__name__)}", {cls.__name__})'
        for cls in sorted_entities
    )

    return f'''
class _Stub:
    @staticmethod
    def _rpc_kwargs(kw):
        # Relation arguments may be passed as stub objects; the host expects
        # "<field>_id" strings.
        out = {{}}
        for k, v in kw.items():
            if isinstance(v, _Stub):
                out[k + "_id"] = v.id
            else:
                out[k] = v
        return out

    def __getattr__(self, name):
        try:
            data = object.__getattribute__(self, "_data")
        except AttributeError as exc:
            raise AttributeError(name) from exc
        if name in data:
            return data[name]
        rel_id = name + "_id"
        if rel_id in data:
            return data[rel_id]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name.startswith("_") or name == "id":
            object.__setattr__(self, name, value)
            return
        data = object.__getattribute__(self, "_data")
        rpc("{orm_update}", _entity=type(self).__name__, id=data["id"], **_Stub._rpc_kwargs({{name: value}}))
        data[name] = value

    @property
    def id(self):
        return object.__getattribute__(self, "_data")["id"]

    @property
    def _id(self):
        return self.id

    def update(self, **fields):
        data = object.__getattribute__(self, "_data")
        rpc("{orm_update}", _entity=type(self).__name__, id=data["id"], **_Stub._rpc_kwargs(fields))
        data.update(fields)

    def delete(self):
        data = object.__getattribute__(self, "_data")
        rpc("{orm_delete}", _entity=type(self).__name__, id=data["id"])

    def __repr__(self):
        data = object.__getattribute__(self, "_data")
        cls = type(self).__name__
        return f"{{cls}}({{data!r}})"


def _make_entity_class(name, prefix, base):
    # Constructor: TodoList(title="...") → create RPC (matches ic-python-db).
    def __init__(self, **kw):
        data = rpc("{orm_create}", _entity=name, **_Stub._rpc_kwargs(kw))
        object.__setattr__(self, "_data", dict(data))

    # Class subscription: TodoList["1"] → load RPC (matches ic-python-db).
    def __class_getitem__(cls, key):
        d = rpc("{orm_get}", _entity=name, id=str(key))
        return cls._wrap(d) if d else None

    attrs = {{
        "_prefix": prefix,
        "__init__": __init__,
        "__class_getitem__": __class_getitem__,
    }}
    for attr in dir(_Stub):
        if attr in attrs:
            continue
        if attr.startswith("_") and attr not in ("_prefix", "_wrap", "_id"):
            continue
        attrs[attr] = getattr(_Stub, attr)
    for attr in dir(base):
        if attr in attrs:
            continue
        if attr.startswith("_") and attr not in ("_prefix", "_wrap", "_id"):
            continue
        attrs[attr] = getattr(base, attr)
    return type(name, (_Stub,), attrs)


{classes_section}


# Rebuild classes with constructor + class-subscription support.
{rebuild_lines}


def eval_repl(code):
    import sys
    from io import StringIO

    buf = StringIO()
    err = StringIO()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = buf
    sys.stderr = err
    # Fresh namespace per call — REPL variables must not leak across __shell__ invocations.
    ns = {{"rpc": globals().get("rpc"), "__builtins__": __builtins__}}
    for _name in ({entity_names},):
        ns[_name] = globals()[_name]
    result = None
    use_eval = False
    code_stripped = code.strip()
    if code_stripped and "\\n" not in code_stripped:
        try:
            compile(code_stripped, "<repl>", "eval")
            use_eval = True
        except SyntaxError:
            use_eval = False
    try:
        if use_eval:
            result = eval(compile(code_stripped, "<repl>", "eval"), ns, ns)
        else:
            exec(compile(code, "<repl>", "exec"), ns, ns)
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
    out = buf.getvalue()
    err_out = err.getvalue()
    if err_out:
        out += err_out
    if use_eval and result is not None:
        out += repr(result) + "\\n"
    return out
'''


def setup_secure_orm(
    entities: list,
    namespace: str,
    principal_type: str = "User",
    principal_entity: Optional[Type[Entity]] = None,
    extra_policies: str = "",
    budget: int = 50_000_000,
    memberships: Optional[Dict[str, List[str]]] = None,
    actions: Optional[Dict[str, str]] = None,
    context: Optional[Dict[str, str]] = None,
    shell_context: Optional[Dict[str, Any]] = None,
) -> "SecureORM":
    """Wire Cedar, policies, and RPC dispatch for *entities*.

    ``memberships``/``actions``/``context`` pass through to
    ``generate_cedar_schema`` so hosts can keep their own action vocabulary
    and request-context shape. ``shell_context`` is merged into every
    REPL-originated authorization request (e.g. ``{"repl": True}``), letting
    policies distinguish sandboxed-REPL calls from ordinary host calls.
    """
    if CedarEngine is None or Slicer is None:
        raise ImportError(
            "cedar_engine and cedar_slicing are required for setup_secure_orm"
        )

    entity_types = {cls.__name__: cls for cls in entities}
    if principal_entity is not None:
        entity_types[principal_type] = principal_entity

    schema_dict = build_schema(entity_types)
    cedar_schema, _report = generate_cedar_schema(
        schema_dict,
        namespace=namespace,
        principal_type=principal_type,
        memberships=memberships,
        actions=actions,
        context=context,
    )
    policies = generate_default_policies(
        schema_dict, namespace, entities, principal_type
    )
    slicer = Slicer(namespace, cedar_schema, principal_type)
    engine = CedarEngine(
        namespace,
        principal_type,
        cedar_schema,
        policies,
        slicer=slicer,
    )
    engine.load(extra_policies)

    return SecureORM(
        engine=engine,
        namespace=namespace,
        entities=list(entities),
        schema=schema_dict,
        principal_type=principal_type,
        principal_entity=principal_entity,
        budget=budget,
        shell_context=shell_context,
    )


class SecureORM:
    """Host-side secure ORM: Cedar checks, RPC dispatch, sandboxed REPL."""

    def __init__(
        self,
        engine: Any,
        namespace: str,
        entities: List[Type[SecureEntity]],
        schema: Dict[str, Any],
        principal_type: str = "User",
        principal_entity: Optional[Type[Entity]] = None,
        budget: int = 50_000_000,
        shell_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.engine = engine
        self.namespace = namespace
        self._entities = list(entities)
        self._schema = schema
        self._principal_type = principal_type
        self._principal_entity = principal_entity
        self._budget = budget
        self._shell_context = shell_context
        self._stub_source = _generate_stub_source(self._entities, self._schema)
        self._sandbox_hash = ""
        self._sandboxes: Dict[str, Any] = {}
        self._prefix_map = {_snake_case(cls.__name__): cls for cls in self._entities}
        self._name_map = {cls.__name__: cls for cls in self._entities}

    def actions(self) -> List[str]:
        # Six generic RPC verbs — the C sandbox gate allows at most 32 actions,
        # and per-entity prefixes would exceed that for a full ggg schema.
        return [_orm_rpc(op) for op in _CRUD]

    def stub_source(self) -> str:
        return self._stub_source

    def status(self) -> dict:
        out = dict(self.engine.status())
        counts: Dict[str, int] = {}
        for cls in self._entities:
            try:
                counts[cls.__name__] = len(list(cls.instances()))
            except Exception:
                counts[cls.__name__] = -1
        out["entities"] = counts
        return out

    def reload_policies(self, extra_policies: str = "") -> dict:
        """Reload Cedar with *extra_policies* appended to auto-generated base policies."""
        ok = self.engine.load(extra_policies)
        out = dict(self.engine.snapshot())
        out["ok"] = ok
        return out

    def cedar(self, query: str) -> str:
        """Read-only Cedar introspection for a ``__cedar__`` query endpoint.

        Accepts JSON ``{"action": ...}`` or a plain action name. Actions:

        - ``snapshot`` (default) — schema, base/extra/effective policies, status
        - ``policies`` — policy text only (base, extra, effective)
        - ``schema`` — Cedar schema source
        - ``status`` — engine availability/enforcement/warnings
        """
        import json

        raw = (query or "").strip()
        if not raw:
            q: Dict[str, Any] = {"action": "snapshot"}
        elif raw.startswith("{"):
            try:
                q = json.loads(raw)
            except Exception:
                return json.dumps({"error": "invalid JSON"})
        else:
            q = {"action": raw}

        action = q.get("action", "snapshot")
        if action == "snapshot":
            return json.dumps(self.engine.snapshot())
        if action == "schema":
            return json.dumps({"schema": self.engine.schema})
        if action == "policies":
            return json.dumps(
                {
                    "base_policies": self.engine.policies,
                    "extra_policies": self.engine.extra_policies(),
                    "policies": self.engine.effective_policies(),
                }
            )
        if action == "status":
            return json.dumps(self.engine.status())
        return json.dumps(
            {
                "error": f"unknown action {action!r}",
                "actions": ["snapshot", "schema", "policies", "status"],
            }
        )

    def _row_dict(self, cls: Type[SecureEntity], row: Entity) -> Dict[str, Any]:
        descriptor = self._schema.get(cls.__name__, {})
        data: Dict[str, Any] = {"id": row._id}

        for name, field_desc in sorted(descriptor.get("fields", {}).items()):
            if field_desc.get("type") not in _SCALAR_TYPES:
                continue
            if hasattr(row, name):
                value = getattr(row, name)
                if isinstance(value, (str, int, bool)):
                    data[name] = value

        for rel_name, rel_desc in sorted(descriptor.get("relationships", {}).items()):
            if rel_desc.get("type") not in ("ManyToOne", "OneToOne"):
                continue
            if hasattr(row, rel_name):
                related = getattr(row, rel_name)
                if related is not None:
                    data[f"{rel_name}_id"] = related._id
        return data

    def _load_row(self, cls: Type[SecureEntity], row_id: str) -> Entity:
        try:
            row = cls.load(row_id)
        except Exception as exc:
            raise RpcError(f"{cls.__name__} {row_id} not found") from exc
        if row is None:
            raise RpcError(f"{cls.__name__} {row_id} not found")
        return row

    def _resolve_relations(
        self, cls: Type[SecureEntity], kwargs: Dict[str, Any]
    ) -> Dict[str, Any]:
        descriptor = self._schema.get(cls.__name__, {})
        resolved: Dict[str, Any] = {}
        for rel_name, rel_desc in descriptor.get("relationships", {}).items():
            if rel_desc.get("type") != "ManyToOne":
                continue
            id_key = f"{rel_name}_id"
            if id_key not in kwargs:
                continue
            targets = _relation_targets(rel_desc)
            if len(targets) != 1:
                continue
            target_cls = self._name_map.get(targets[0])
            if target_cls is None:
                raise RpcError(f"unknown relation target {targets[0]!r}")
            related_id = str(kwargs.pop(id_key))
            resolved[rel_name] = self._load_row(target_cls, related_id)
        return resolved

    def _create(self, cls: Type[SecureEntity], principal_id: str, kwargs: Dict[str, Any]) -> dict:
        kwargs = dict(kwargs or {})
        kwargs.pop("id", None)
        self.engine.check(
            principal_id, "entity.create", cls.__name__, "new",
            context=self._shell_context,
        )

        owner_field = getattr(cls, "__owner_field__", "owner")
        fields = self._schema.get(cls.__name__, {}).get("fields", {})
        relationships = self._schema.get(cls.__name__, {}).get("relationships", {})
        force_owner_relation = False
        if self._principal_entity is not None and owner_field not in fields:
            owner_rel = relationships.get(owner_field)
            if owner_rel is not None and owner_rel.get("type") in (
                "ManyToOne",
                "OneToOne",
            ):
                targets = _relation_targets(owner_rel)
                if targets == [self.engine.principal_type]:
                    force_owner_relation = True
                    kwargs.pop(f"{owner_field}_id", None)
                    kwargs.pop(owner_field, None)

        if owner_field in fields:
            kwargs[owner_field] = principal_id
        else:
            kwargs.pop(owner_field, None)

        relation_kwargs = self._resolve_relations(cls, kwargs)

        if force_owner_relation:
            principal_cls = self._principal_entity
            principal_row = None
            try:
                principal_row = principal_cls[principal_id]
            except (KeyError, TypeError, IndexError):
                principal_row = None
            if principal_row is None and hasattr(principal_cls, "find_by"):
                rows, _ = principal_cls.find_by("id", principal_id, count=1)
                principal_row = rows[0] if rows else None
            if principal_row is None and hasattr(principal_cls, "load"):
                principal_row = principal_cls.load(principal_id)
            if principal_row is None:
                raise RpcError(
                    f"{self.engine.principal_type} {principal_id} not found"
                )
            relation_kwargs[owner_field] = principal_row

        # Creating a child row mutates the parent's content, so every resolved
        # ManyToOne parent with an owner field must pass an update check —
        # otherwise anyone could attach rows to another user's entities.
        for parent_row in relation_kwargs.values():
            parent_cls = type(parent_row)
            parent_fields = self._schema.get(parent_cls.__name__, {}).get("fields", {})
            parent_owner = getattr(parent_cls, "__owner_field__", "owner")
            if parent_owner in parent_fields:
                self.engine.check(
                    principal_id,
                    "entity.update",
                    parent_cls.__name__,
                    parent_row._id,
                    parent_row,
                    context=self._shell_context,
                )
        scalar_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k in fields and fields[k].get("type") in _SCALAR_TYPES
        }
        row = cls(**scalar_kwargs, **relation_kwargs)
        return self._row_dict(cls, row)

    def _visible_rows(
        self, cls: Type[SecureEntity], principal_id: str, kwargs: Dict[str, Any]
    ) -> List[Entity]:
        """Rows of *cls* matching relation/scalar filters and Cedar `entity.list`."""
        kwargs = dict(kwargs or {})
        descriptor = self._schema.get(cls.__name__, {})

        filter_rels: Dict[str, str] = {}
        for rel_name, rel_desc in descriptor.get("relationships", {}).items():
            if rel_desc.get("type") != "ManyToOne":
                continue
            id_key = f"{rel_name}_id"
            if id_key in kwargs:
                filter_rels[rel_name] = str(kwargs.pop(id_key))

        # Remaining kwargs are equality filters on attributes (native `find`
        # semantics: unknown attributes simply never match).
        scalar_filters = dict(kwargs)

        rows = list(cls.instances())
        filtered: List[Entity] = []
        for row in rows:
            match = True
            for rel_name, rel_id in filter_rels.items():
                related = getattr(row, rel_name, None)
                if related is None or related._id != rel_id:
                    match = False
                    break
            if match:
                for field_name, value in scalar_filters.items():
                    if getattr(row, field_name, None) != value:
                        match = False
                        break
            if match:
                filtered.append(row)

        return [
            row
            for row in filtered
            if self.engine.is_authorized(
                principal_id, "entity.list", cls.__name__, row._id, row,
                context=self._shell_context,
            )
        ]

    def _list(self, cls: Type[SecureEntity], principal_id: str, kwargs: Dict[str, Any]) -> list:
        kwargs = dict(kwargs or {})
        # Authorize list even when the table is empty — forbidden types must
        # not appear to succeed with an empty result.
        self.engine.check(
            principal_id,
            "entity.list",
            cls.__name__,
            "",
            None,
            context=self._shell_context,
        )
        # Pagination over *visible* rows (applied after Cedar, so page sizes do
        # not leak the existence of hidden rows).
        from_id = int(kwargs.pop("from_id", 1) or 1)
        count = kwargs.pop("count", None)
        count = int(count) if count is not None else None

        rows = self._visible_rows(cls, principal_id, kwargs)
        if from_id > 1:
            rows = [r for r in rows if r._id.isdigit() and int(r._id) >= from_id]
        if count is not None:
            rows = rows[:count]
        return [self._row_dict(cls, row) for row in rows]

    def _count(self, cls: Type[SecureEntity], principal_id: str, kwargs: Dict[str, Any]) -> int:
        return len(self._visible_rows(cls, principal_id, kwargs or {}))

    def _get(self, cls: Type[SecureEntity], principal_id: str, kwargs: Dict[str, Any]) -> Optional[dict]:
        row_id = str(kwargs.get("id", ""))
        row = cls.load(row_id)
        if row is None:
            return None
        self.engine.check(
            principal_id, "entity.get", cls.__name__, row_id, row,
            context=self._shell_context,
        )
        return self._row_dict(cls, row)

    def _update(self, cls: Type[SecureEntity], principal_id: str, kwargs: Dict[str, Any]) -> dict:
        kwargs = dict(kwargs or {})
        row_id = str(kwargs.pop("id", ""))
        row = self._load_row(cls, row_id)
        self.engine.check(
            principal_id, "entity.update", cls.__name__, row_id, row,
            context=self._shell_context,
        )

        owner_field = getattr(cls, "__owner_field__", "owner")
        kwargs.pop("id", None)
        kwargs.pop(owner_field, None)

        fields = self._schema.get(cls.__name__, {}).get("fields", {})
        relationships = self._schema.get(cls.__name__, {}).get("relationships", {})

        for name, value in kwargs.items():
            if name in fields and fields[name].get("type") in _SCALAR_TYPES:
                setattr(row, name, value)
            elif name in relationships:
                continue
        return self._row_dict(cls, row)

    def _delete(self, cls: Type[SecureEntity], principal_id: str, kwargs: Dict[str, Any]) -> dict:
        row_id = str(kwargs.get("id", ""))
        row = self._load_row(cls, row_id)
        self.engine.check(
            principal_id, "entity.delete", cls.__name__, row_id, row,
            context=self._shell_context,
        )
        # Cascade to OneToMany children: they are owned through this row, so the
        # check above already covers them.
        descriptor = self._schema.get(cls.__name__, {})
        for rel_name, rel_desc in descriptor.get("relationships", {}).items():
            if rel_desc.get("type") != "OneToMany":
                continue
            try:
                children = list(getattr(row, rel_name) or [])
            except Exception:
                continue
            for child in children:
                child.delete()
        row.delete()
        return {"deleted": row_id}

    def handle_rpc(self, principal_id: str, action: str, kwargs: dict) -> Any:
        """Dispatch one sandbox RPC action under Cedar enforcement."""
        kwargs = dict(kwargs or {})
        if action.startswith(f"{_ORM_RPC_PREFIX}."):
            op = action[len(_ORM_RPC_PREFIX) + 1 :]
            entity_name = kwargs.pop("_entity", None)
            if not entity_name:
                raise RpcError(f"unknown action {action!r}")
            cls = self._name_map.get(str(entity_name))
            if cls is None:
                raise RpcError(f"unknown entity {entity_name!r}")
            if op == "create":
                return self._create(cls, principal_id, kwargs)
            if op == "list":
                return self._list(cls, principal_id, kwargs)
            if op == "count":
                return self._count(cls, principal_id, kwargs)
            if op == "get":
                return self._get(cls, principal_id, kwargs)
            if op == "update":
                return self._update(cls, principal_id, kwargs)
            if op == "delete":
                return self._delete(cls, principal_id, kwargs)
            raise RpcError(f"unknown action {action!r}")

        if "." not in action:
            raise RpcError(f"unknown action {action!r}")
        prefix, op = action.rsplit(".", 1)
        cls = self._prefix_map.get(prefix)
        if cls is None:
            raise RpcError(f"unknown action {action!r}")

        if op == "create":
            return self._create(cls, principal_id, kwargs or {})
        if op == "list":
            return self._list(cls, principal_id, kwargs or {})
        if op == "count":
            return self._count(cls, principal_id, kwargs or {})
        if op == "get":
            return self._get(cls, principal_id, kwargs or {})
        if op == "update":
            return self._update(cls, principal_id, kwargs or {})
        if op == "delete":
            return self._delete(cls, principal_id, kwargs or {})
        raise RpcError(f"unknown action {action!r}")

    def shell(self, code: str) -> str:
        """Run *code* in a per-principal sandbox; mutations go through handle_rpc."""
        try:
            import _basilisk_sandbox as sb
        except ImportError as exc:
            raise RuntimeError("_basilisk_sandbox is not available") from exc

        _ensure_basilisk_sandbox()
        try:
            from basilisk import ic
            from basilisk.sandbox import (
                BudgetExceeded,
                build_capability,
                call_sandboxed,
                spawn_sandboxed,
            )
        except ImportError as exc:
            raise RuntimeError("basilisk sandbox helpers are not available") from exc

        principal_id = str(ic.caller())

        if not self._sandbox_hash:
            self._sandbox_hash = sb.sha256(self._stub_source)
            sb.approve_hash(self._sandbox_hash)

        manifest = {"classes": {}, "allowed_actions": self.actions()}
        cap = build_capability(manifest, manifest, context_id=principal_id)

        def handler(context_id: str, action: str, kwargs: dict) -> Any:
            if context_id != principal_id:
                raise PermissionError("context mismatch")
            try:
                return self.handle_rpc(principal_id, action, kwargs or {})
            except PermissionError as exc:
                raise PermissionError(
                    _rpc_deny_message(action, kwargs or {}, exc)
                ) from exc
            except RpcError as exc:
                raise RuntimeError(str(exc)) from exc

        handle = self._sandboxes.get(principal_id)
        if handle is None:
            handle = spawn_sandboxed(
                self._stub_source,
                self._sandbox_hash,
                cap,
                handler,
                budget=self._budget,
            )
            self._sandboxes[principal_id] = handle

        try:
            result = call_sandboxed(handle, "eval_repl", {"code": code})
        except BudgetExceeded as exc:
            return f"BudgetExceeded: {exc}\n"
        except (RuntimeError, PermissionError) as exc:
            return _format_sandbox_error(str(exc)) + "\n"

        if isinstance(result, str):
            return result
        if result is None:
            return ""
        return repr(result) + "\n"
