"""Generate a Cedar schema from ic-python-db Entity definitions.

The schema Cedar validates policies against has to describe the same entities the
ORM actually stores, so it is derived from ``build_schema()`` rather than written
by hand. A renamed field then breaks schema generation instead of silently
turning a policy condition into a type error at decision time.

Two things are deliberately *not* inferred:

``memberships``
    Which relations become Cedar's ``in`` hierarchy. ``principal in
    resource.department`` is a membership test against an entity's parents, and
    only the application knows which relations are meant to grant membership.
    Treating every foreign key as a parent would make ancestor sets enormous and
    the semantics accidental.

``actions``
    Cedar actions are an authorization vocabulary, not a data model, so they are
    supplied by the caller.

Anything that cannot be represented is reported rather than dropped quietly —
see :class:`GenerationReport`.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

# ic-python-db property types that have a Cedar counterpart.
TYPE_MAP = {
    "String": "String",
    "Integer": "Long",
    "Boolean": "Bool",
}

# Cedar has no floating point type, and `decimal` is a fixed-precision extension
# type rather than a drop-in. EncryptedString holds ciphertext, which must never
# be an authorization input in the first place.
UNREPRESENTABLE = {
    "Float": "Cedar has no floating point type",
    "EncryptedString": "ciphertext is not an authorization input",
}

# Only single-valued relations become attributes. OneToMany and ManyToMany are
# stored as reverse indexes, so materialising them as sets would pull unbounded
# collections into a decision — the cost this design exists to avoid.
SINGLE_VALUED = {"ManyToOne", "OneToOne"}

# Reserved in Cedar's grammar, so an attribute of this name must be quoted.
RESERVED = {
    "true",
    "false",
    "if",
    "then",
    "else",
    "in",
    "is",
    "like",
    "has",
    "permit",
    "forbid",
    "when",
    "unless",
    "principal",
    "action",
    "resource",
    "context",
}

DEFAULT_ACTIONS = {
    "entity.get": "read",
    "entity.list": "read",
    "entity.create": "write",
    "entity.update": "write",
    "entity.delete": "write",
}


@dataclass
class GenerationReport:
    """What was generated, and what could not be."""

    entities: int = 0
    attributes: int = 0
    skipped_fields: List[Tuple[str, str, str]] = field(default_factory=list)
    skipped_relations: List[Tuple[str, str, str]] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{self.entities} entity types, {self.attributes} attributes",
            f"{len(self.skipped_fields)} fields and "
            f"{len(self.skipped_relations)} relations not represented",
        ]
        for entity, name, reason in self.skipped_fields + self.skipped_relations:
            lines.append(f"  {entity}.{name}: {reason}")
        return "\n".join(lines)


def manifest_to_schema(entities: Mapping[str, Any]) -> Dict[str, Any]:
    """Convert an extension manifest's ``entities`` block to a schema descriptor.

    Extension-owned entities are declared in the manifest rather than as Python
    classes, so they never pass through ``build_schema()``. Reshaping them here
    means both kinds of entity go through one generator, and a Cedar schema means
    the same thing whichever side declared it.
    """
    schema: Dict[str, Any] = {}
    for name, spec in entities.items():
        fields = {
            field_name: {"kind": "property", "type": field_spec.get("type")}
            for field_name, field_spec in (spec.get("fields") or {}).items()
        }
        schema[name] = {"version": 1, "fields": fields, "relationships": {}}
    return schema


def generate_extension_schema(
    ext_id: str,
    entities: Mapping[str, Any],
    principal_type: str = "Realm::User",
    actions: Optional[Dict[str, str]] = None,
    context: Optional[Dict[str, str]] = None,
) -> Tuple[str, GenerationReport]:
    """Render the Cedar namespace for one extension's own entities.

    The namespace matches what ``ic-python-db`` already stores: an extension's
    entities live under ``ext_<name>``, so a Cedar type and a storage key agree
    without a translation table.

    Actions are declared *inside* this namespace and apply only to these
    resources. That is what contains an extension: validated against this schema
    alone, a policy naming any type outside the namespace is a validation error
    rather than something a reviewer has to notice. The one deliberate exception
    is the principal, which is the realm's user.
    """
    return generate_cedar_schema(
        manifest_to_schema(entities),
        namespace=extension_namespace(ext_id),
        principal_type=principal_type,
        actions=actions,
        context=context,
    )


def extension_namespace(ext_id: str) -> str:
    """The Cedar namespace for an extension, matching its storage namespace."""
    return f"ext_{ext_id}"


def _attr_name(name: str) -> str:
    if name in RESERVED or not name.isidentifier():
        return f'"{name}"'
    return name


def _relation_targets(descriptor: Dict[str, Any]) -> List[str]:
    """Relation targets, which build_schema gives as either a str or a list."""
    target = descriptor.get("target")
    if target is None:
        return []
    return list(target) if isinstance(target, list) else [target]


def _resolve_memberships(
    entity_type: str,
    relation_names: List[str],
    relationships: Dict[str, Any],
) -> List[str]:
    """Turn nominated relation names into the parent types they point at.

    Nominating a relation that does not exist is an error rather than a silent
    omission: it means the hierarchy config has drifted from the entity
    definitions, and a missing parent silently turns every ``in`` test false.
    """
    parents: List[str] = []
    for relation_name in relation_names:
        descriptor = relationships.get(relation_name)
        if descriptor is None:
            raise ValueError(
                f"{entity_type}: membership relation {relation_name!r} does not "
                f"exist. Known relations: {sorted(relationships)}"
            )
        targets = _relation_targets(descriptor)
        if not targets:
            raise ValueError(f"{entity_type}.{relation_name} has no target entity type")
        parents.extend(targets)
    # Deduplicate while keeping the caller's order, so output stays stable.
    return list(dict.fromkeys(parents))


def generate_cedar_schema(
    schema: Dict[str, Any],
    namespace: str,
    principal_type: str,
    memberships: Optional[Dict[str, List[str]]] = None,
    actions: Optional[Dict[str, str]] = None,
    context: Optional[Dict[str, str]] = None,
) -> Tuple[str, GenerationReport]:
    """Render a Cedar schema for the entity types in ``schema``.

    Args:
        schema: Output of ``ic_python_db.schema.build_schema()``.
        namespace: Cedar namespace, e.g. ``"Realm"``.
        principal_type: Entity type that authenticates, e.g. ``"User"``.
        memberships: Entity type -> relation names whose targets become Cedar
            parents, e.g. ``{"User": ["departments", "profiles"]}``.
        actions: Action name -> group name. Defaults to generic CRUD.
        context: Attribute name -> Cedar type for the request context, e.g.
            ``{"extension": "String"}``. Context carries facts about the request
            rather than the data, which is how a policy can distinguish who is
            asking from what is being asked about.

    Returns:
        The schema text, and a report of what could not be represented.
    """
    # A qualified name means the principal lives in another namespace, which is
    # the normal case for an extension: it authorizes the realm's users, not
    # some notion of its own.
    if "::" not in principal_type and principal_type not in schema:
        raise ValueError(
            f"principal type {principal_type!r} is not among the entity types"
        )

    # ic-python-db namespaces a type as ``namespace::Class``, which is also
    # Cedar's namespace separator. Such a type belongs in its own Cedar
    # namespace block, not inside this one, so refuse rather than emit a name
    # Cedar will misparse.
    namespaced = sorted(t for t in schema if "::" in t)
    if namespaced:
        raise ValueError(
            f"entity types belong to another namespace and cannot be generated "
            f"into {namespace!r}: {namespaced}"
        )

    memberships = memberships or {}
    actions = DEFAULT_ACTIONS if actions is None else actions
    report = GenerationReport()
    known = set(schema)

    unknown_membership_types = set(memberships) - known
    if unknown_membership_types:
        raise ValueError(
            f"memberships reference unknown entity types: "
            f"{sorted(unknown_membership_types)}"
        )

    blocks: List[str] = []

    for entity_type in sorted(schema):
        descriptor = schema[entity_type]
        relationships = descriptor.get("relationships", {})
        attributes: List[str] = []

        for field_name in sorted(descriptor.get("fields", {})):
            field_desc = descriptor["fields"][field_name]
            python_type = field_desc.get("type")
            if python_type in UNREPRESENTABLE:
                report.skipped_fields.append(
                    (entity_type, field_name, UNREPRESENTABLE[python_type])
                )
                continue
            cedar_type = TYPE_MAP.get(python_type)
            if cedar_type is None:
                report.skipped_fields.append(
                    (entity_type, field_name, f"unmapped property type {python_type}")
                )
                continue
            # Every attribute is optional: the ORM leaves unset fields absent
            # from the stored row, and a policy reading a missing required
            # attribute is an evaluation error rather than a denial.
            attributes.append(f"    {_attr_name(field_name)}?: {cedar_type},")
            report.attributes += 1

        for relation_name in sorted(relationships):
            relation = relationships[relation_name]
            relation_type = relation.get("type")
            if relation_type not in SINGLE_VALUED:
                report.skipped_relations.append(
                    (entity_type, relation_name, f"{relation_type} is multi-valued")
                )
                continue
            targets = _relation_targets(relation)
            if len(targets) != 1:
                report.skipped_relations.append(
                    (entity_type, relation_name, f"expected one target, got {targets}")
                )
                continue
            target = targets[0]
            if target not in known:
                report.skipped_relations.append(
                    (entity_type, relation_name, f"target {target} is not registered")
                )
                continue
            attributes.append(f"    {_attr_name(relation_name)}?: {target},")
            report.attributes += 1

        parents = _resolve_memberships(
            entity_type, memberships.get(entity_type, []), relationships
        )
        missing_parents = [p for p in parents if p not in known]
        if missing_parents:
            raise ValueError(
                f"{entity_type}: membership targets not registered: {missing_parents}"
            )

        header = f"entity {entity_type}"
        if parents:
            header += f" in [{', '.join(parents)}]"

        if attributes:
            blocks.append(header + " {\n" + "\n".join(attributes) + "\n};")
        else:
            blocks.append(header + ";")
        report.entities += 1

    resource_types = ", ".join(sorted(schema))
    if context:
        # Every context attribute is optional: a host-originated request simply
        # omits the ones that only apply to extensions, and a policy tests with
        # `context has ...` before reading.
        rendered = ", ".join(
            f"{_attr_name(name)}?: {cedar_type}"
            for name, cedar_type in sorted(context.items())
        )
        context_clause = f", context: {{ {rendered} }}"
    else:
        context_clause = ""

    action_blocks: List[str] = []
    # Group actions carry the same `appliesTo` as their members, so they can be
    # requested directly and not only used to group. A host mapping many
    # operations onto a coarse `read`/`write` needs that: an action declared
    # without `appliesTo` applies to nothing, so a request naming it is refused
    # by the schema and the caller sees a denial with no policy behind it.
    for group in sorted(set(actions.values())):
        action_blocks.append(
            f"action {group}\n"
            f"    appliesTo {{ principal: [{principal_type}], "
            f"resource: [{resource_types}]{context_clause} }};"
        )
    for action_name in sorted(actions):
        group = actions[action_name]
        if action_name == group:
            # A self-grouped action is already emitted by the group loop above;
            # re-declaring it here would duplicate the name in the schema.
            continue
        action_blocks.append(
            f'action "{action_name}" in [{group}]\n'
            f"    appliesTo {{ principal: [{principal_type}], "
            f"resource: [{resource_types}]{context_clause} }};"
        )

    body = "\n\n".join(blocks + action_blocks)
    indented = "\n".join(
        ("    " + line) if line.strip() else line for line in body.split("\n")
    )
    text = f"namespace {namespace} {{\n{indented}\n}}\n"
    return text, report
