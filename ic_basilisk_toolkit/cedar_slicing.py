"""Generic Cedar entity-slice builder, parameterized by namespace and schema.

Projection rules (API contracts):

- Only attributes the schema declares for that type are projected. Cedar rejects
  the whole store on undeclared attrs.
- Scalars only: ``str``, ``bool``, ``int``. Skip callables, lists, tuples, dicts,
  sets, floats (Cedar has no float).
- Skip underscore-prefixed names and anything in :data:`NEVER_PROJECT`.
- Single-valued relation values (objects with a string ``id`` or ``_id`` attr
  whose type name is declared) become ``{"__entity": uid_json(...)}`` references.
  For the row's own id, callers pass ``entity_id`` explicitly.
- A ``getattr`` that raises is skipped, never fails the decision.
- Schema parsing is hand-rolled (no ``re`` module — the WASI sandbox lacks
  ``re.compile``).
"""

from typing import Any, Dict, List, Optional

NEVER_PROJECT = frozenset({"password", "secret", "private_key", "ciphertext"})

_SCALARS = (str, bool, int)


class Slicer:
    """Build the smallest Cedar entity store needed for one decision."""

    def __init__(
        self,
        namespace: str,
        schema: str,
        principal_type: str = "User",
    ) -> None:
        self.namespace = namespace
        self.schema = schema
        self.principal_type = principal_type
        self._cache: Dict[str, Any] = {}

    def uid(self, entity_type: str, entity_id: str) -> str:
        """A Cedar entity uid as text, e.g. ``TodoApp::User::"alice"``."""
        return f'{self.namespace}::{entity_type}::"{entity_id}"'

    def uid_json(self, entity_type: str, entity_id: str) -> Dict[str, str]:
        """A Cedar entity uid as JSON, which is what the entity store expects."""
        return {"type": f"{self.namespace}::{entity_type}", "id": entity_id}

    def declared_types(self) -> frozenset:
        """Entity type names declared in the schema."""
        cached = self._cache.get("types")
        if cached is not None:
            return cached
        types = set()
        for line in self.schema.splitlines():
            line = line.strip()
            if line.startswith("entity "):
                name = line[len("entity ") :].split(" in ")[0]
                types.add(name.split("{")[0].split(";")[0].strip())
        resolved = frozenset(types)
        self._cache["types"] = resolved
        return resolved

    def declared_attrs(self, entity_type: str) -> Optional[frozenset]:
        """Attribute names declared for *entity_type*, or ``None`` if undeclared."""
        cache_key = "attrs:" + entity_type
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        result: Optional[frozenset] = None
        marker = "entity " + entity_type
        idx = 0
        while True:
            idx = self.schema.find(marker, idx)
            if idx == -1:
                break
            end = idx + len(marker)
            if end < len(self.schema) and (
                self.schema[end].isalnum() or self.schema[end] == "_"
            ):
                idx = end
                continue
            brace = self.schema.find("{", end)
            semi = self.schema.find(";", end)
            if semi != -1 and (brace == -1 or semi < brace):
                result = frozenset()
                break
            if brace == -1:
                break
            close = self.schema.find("}", brace)
            if close == -1:
                break
            names = set()
            for piece in self.schema[brace + 1 : close].split(","):
                piece = piece.strip()
                if not piece:
                    continue
                name = piece.split("?:")[0].split(":")[0].strip()
                if name:
                    names.add(name)
            result = frozenset(names)
            break
        self._cache[cache_key] = result
        return result

    def _entity_type_from_uid_json(self, uid: Dict[str, str]) -> str:
        type_field = uid.get("type", "")
        if "::" in type_field:
            return type_field.split("::", 1)[1]
        return type_field

    def _reference(self, value: Any) -> Optional[Dict[str, Any]]:
        """A related row as a Cedar entity reference, or ``None`` if it is not one."""
        related_id = getattr(value, "id", None)
        if not isinstance(related_id, str) or not related_id:
            related_id = getattr(value, "_id", None)
        if not isinstance(related_id, str) or not related_id:
            return None
        type_name = type(value).__name__
        if type_name not in self.declared_types():
            return None
        return {"__entity": self.uid_json(type_name, related_id)}

    def _related_from_value(self, value: Any) -> Optional[tuple]:
        """Return ``(type_name, related_id, value)`` for a single-valued relation."""
        related_id = getattr(value, "id", None)
        if not isinstance(related_id, str) or not related_id:
            related_id = getattr(value, "_id", None)
        if not isinstance(related_id, str) or not related_id:
            return None
        type_name = type(value).__name__
        if type_name not in self.declared_types():
            return None
        return (type_name, related_id, value)

    def _attrs(
        self, row: Any, declared: Optional[frozenset] = None
    ) -> Dict[str, Any]:
        """Project scalar fields and single-valued references from a row."""
        out: Dict[str, Any] = {}
        for name in dir(row):
            if name.startswith("_") or name in NEVER_PROJECT:
                continue
            if declared is not None and name not in declared:
                continue
            try:
                value = getattr(row, name)
            except Exception:
                continue
            if isinstance(value, _SCALARS):
                out[name] = value
                continue
            if callable(value) or isinstance(value, (list, tuple, dict, set, float)):
                continue
            reference = self._reference(value)
            if reference is not None:
                out[name] = reference
        return out

    def _collect_relations(
        self, row: Any, declared: Optional[frozenset]
    ) -> List[tuple]:
        """Find declared single-valued relation objects on a row."""
        related: List[tuple] = []
        if row is None or declared is None:
            return related
        for name in declared:
            if name.startswith("_") or name in NEVER_PROJECT:
                continue
            try:
                value = getattr(row, name)
            except Exception:
                continue
            if isinstance(value, _SCALARS):
                continue
            if callable(value) or isinstance(value, (list, tuple, dict, set, float)):
                continue
            match = self._related_from_value(value)
            if match is not None:
                related.append(match)
        return related

    def _entity(
        self,
        entity_type: str,
        entity_id: str,
        attrs: Dict[str, Any],
        parents: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        return {
            "uid": self.uid_json(entity_type, entity_id),
            "attrs": attrs,
            "parents": list(parents or ()),
        }

    def principal_entity(
        self, principal_id: str, parents: list | None = None
    ) -> List[Dict[str, Any]]:
        """The calling principal and optional parent-group entities."""
        parent_uids: List[Dict[str, str]] = list(parents or ())
        entities: List[Dict[str, Any]] = []

        seen = set()
        for parent in parent_uids:
            key = (parent.get("type"), parent.get("id"))
            if key in seen:
                continue
            seen.add(key)
            parent_type = self._entity_type_from_uid_json(parent)
            entities.append(self._entity(parent_type, parent["id"], {}))

        declared = self.declared_attrs(self.principal_type)
        attrs: Dict[str, Any] = {"id": principal_id}
        entities.append(
            self._entity(self.principal_type, principal_id, attrs, parent_uids)
        )
        return entities

    def resource_entity(
        self, entity_type: str, entity_id: str, row=None
    ) -> List[Dict[str, Any]]:
        """The resource being acted on."""
        if not entity_type or not entity_id:
            return []
        declared = self.declared_attrs(entity_type)
        if declared is None:
            return []
        return [
            self._entity(
                entity_type,
                entity_id,
                self._attrs(row, declared) if row is not None else {},
            )
        ]

    def row_entity(
        self, entity_type: str, entity_id: str, row, depth: int = 1
    ) -> List[Dict[str, Any]]:
        """The row entity plus related entities up to *depth*."""
        if not entity_type or not entity_id:
            return []
        declared = self.declared_attrs(entity_type)
        if declared is None:
            return []

        entities: List[Dict[str, Any]] = [
            self._entity(entity_type, entity_id, self._attrs(row, declared))
        ]

        if depth > 0:
            for rel_type, rel_id, rel_row in self._collect_relations(row, declared):
                entities.extend(self.row_entity(rel_type, rel_id, rel_row, depth - 1))

        return entities

    def slice_for(
        self,
        principal_id: str,
        resource_type: str = "",
        resource_id: str = "",
        resource_row=None,
        principal_parents: list | None = None,
    ) -> List[Dict[str, Any]]:
        """The complete entity store for one decision."""
        entities = self.principal_entity(principal_id, principal_parents)
        entities.extend(
            self.resource_entity(resource_type, resource_id, resource_row)
        )
        return entities
