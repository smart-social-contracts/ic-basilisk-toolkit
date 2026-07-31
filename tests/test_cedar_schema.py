"""Unit tests for ic_basilisk_toolkit.cedar_schema — Cedar schema generation.

These are pure-Python unit tests (no canister required). They check the shape of
the generated text; that Cedar itself accepts it is verified separately against
the real parser.

Run: pytest tests/test_cedar_schema.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ic_basilisk_toolkit.cedar_schema import (
    extension_namespace,
    generate_cedar_schema,
    generate_extension_schema,
    manifest_to_schema,
)


def _prop(type_name):
    return {"kind": "property", "type": type_name}


def _rel(type_name, target, many=False):
    return {
        "kind": "relationship",
        "type": type_name,
        "target": target,
        "many": many,
    }


@pytest.fixture
def schema():
    """A miniature stand-in for build_schema() output."""
    return {
        "User": {
            "version": 1,
            "fields": {"id": _prop("String")},
            "relationships": {
                "departments": _rel("ManyToMany", ["Department"], many=True),
            },
        },
        "Department": {
            "version": 1,
            "fields": {
                "name": _prop("String"),
                "is_root": _prop("Boolean"),
                "quorum": _prop("Integer"),
            },
            "relationships": {
                "head": _rel("ManyToOne", "User"),
                "sub_departments": _rel("OneToMany", "Department", many=True),
            },
        },
    }


class TestPropertyMapping:
    def test_property_types_map_onto_cedar_types(self, schema):
        text, _ = generate_cedar_schema(schema, "Realm", "User")
        assert "name?: String," in text
        assert "is_root?: Bool," in text
        assert "quorum?: Long," in text

    def test_attributes_are_optional_because_unset_fields_are_absent(self, schema):
        # A required attribute that is missing is an evaluation error rather than
        # a denial, so nothing may be declared required.
        text, _ = generate_cedar_schema(schema, "Realm", "User")
        assert ": String," not in text.replace("?: String,", "")

    def test_float_is_reported_not_silently_dropped(self, schema):
        schema["Department"]["fields"]["ratio"] = _prop("Float")
        text, report = generate_cedar_schema(schema, "Realm", "User")
        assert "ratio" not in text
        assert ("Department", "ratio", "Cedar has no floating point type") in (
            report.skipped_fields
        )

    def test_ciphertext_is_never_an_authorization_input(self, schema):
        schema["User"]["fields"]["private_data"] = _prop("EncryptedString")
        text, report = generate_cedar_schema(schema, "Realm", "User")
        assert "private_data" not in text
        assert any(f[1] == "private_data" for f in report.skipped_fields)

    def test_unknown_property_type_is_reported(self, schema):
        schema["Department"]["fields"]["weird"] = _prop("Quaternion")
        _, report = generate_cedar_schema(schema, "Realm", "User")
        assert ("Department", "weird", "unmapped property type Quaternion") in (
            report.skipped_fields
        )


class TestRelations:
    def test_single_valued_relations_become_entity_attributes(self, schema):
        text, _ = generate_cedar_schema(schema, "Realm", "User")
        assert "head?: User," in text

    def test_multi_valued_relations_are_skipped(self, schema):
        # Materialising a reverse index as a Set would pull an unbounded
        # collection into every decision.
        text, report = generate_cedar_schema(schema, "Realm", "User")
        assert "sub_departments" not in text
        assert ("Department", "sub_departments", "OneToMany is multi-valued") in (
            report.skipped_relations
        )

    def test_relation_to_unregistered_type_is_skipped(self, schema):
        schema["Department"]["relationships"]["ghost"] = _rel("ManyToOne", "Ghost")
        text, report = generate_cedar_schema(schema, "Realm", "User")
        assert "Ghost" not in text
        assert ("Department", "ghost", "target Ghost is not registered") in (
            report.skipped_relations
        )


class TestMemberships:
    def test_nominated_relation_becomes_a_parent(self, schema):
        text, _ = generate_cedar_schema(
            schema, "Realm", "User", memberships={"User": ["departments"]}
        )
        assert "entity User in [Department]" in text

    def test_without_nomination_there_is_no_hierarchy(self, schema):
        text, _ = generate_cedar_schema(schema, "Realm", "User")
        assert "entity User in" not in text

    def test_nominating_a_missing_relation_is_an_error(self, schema):
        # A silently missing parent turns every `in` test false, which fails open
        # or closed depending on the policy — so it must be loud.
        with pytest.raises(ValueError, match="does not exist"):
            generate_cedar_schema(
                schema, "Realm", "User", memberships={"User": ["typo"]}
            )

    def test_nominating_an_unknown_entity_type_is_an_error(self, schema):
        with pytest.raises(ValueError, match="unknown entity types"):
            generate_cedar_schema(schema, "Realm", "User", memberships={"Ghost": ["x"]})


class TestActions:
    def test_default_actions_cover_generic_crud(self, schema):
        text, _ = generate_cedar_schema(schema, "Realm", "User")
        for action in (
            "entity.get",
            "entity.list",
            "entity.create",
            "entity.update",
            "entity.delete",
        ):
            assert f'action "{action}"' in text

    def test_actions_are_grouped_for_coarse_policies(self, schema):
        text, _ = generate_cedar_schema(schema, "Realm", "User")
        assert "action read\n" in text
        assert "action write\n" in text
        assert 'action "entity.get" in [read]' in text

    def test_group_actions_can_themselves_be_requested(self, schema):
        # An action declared without `appliesTo` applies to nothing, so a
        # request naming it is refused by the schema and the caller sees a
        # denial with no policy behind it. A host that maps many operations onto
        # a coarse read/write needs these to be real, requestable actions.
        text, _ = generate_cedar_schema(schema, "Realm", "User")
        read_block = text.split("action read")[1].split(";")[0]
        assert "appliesTo" in read_block
        assert "principal: [User]" in read_block

    def test_custom_actions_replace_the_defaults(self, schema):
        text, _ = generate_cedar_schema(
            schema, "Realm", "User", actions={"rfp.award": "write"}
        )
        assert 'action "rfp.award" in [write]' in text
        assert "entity.get" not in text

    def test_principal_must_be_a_known_entity_type(self, schema):
        with pytest.raises(ValueError, match="principal type"):
            generate_cedar_schema(schema, "Realm", "Ghost")


class TestContext:
    def test_context_is_declared_on_every_action(self, schema):
        text, _ = generate_cedar_schema(
            schema, "Realm", "User", context={"extension": "String"}
        )
        # The five named actions plus the two groups they belong to, since a
        # group is requestable in its own right and needs the same context.
        assert text.count("context: { extension?: String }") == len(
            {
                "entity.get",
                "entity.list",
                "entity.create",
                "entity.update",
                "entity.delete",
            }
        ) + len({"read", "write"})

    def test_context_attributes_are_optional(self, schema):
        # A host-originated request omits attributes that only apply to
        # extensions, so requiring them would make those requests malformed.
        text, _ = generate_cedar_schema(
            schema, "Realm", "User", context={"extension": "String"}
        )
        assert "extension?: String" in text

    def test_no_context_clause_when_none_requested(self, schema):
        text, _ = generate_cedar_schema(schema, "Realm", "User")
        assert "context:" not in text


class TestOutputShape:
    def test_reserved_attribute_names_are_quoted(self, schema):
        schema["Department"]["fields"]["principal"] = _prop("String")
        text, _ = generate_cedar_schema(schema, "Realm", "User")
        assert '"principal"?: String,' in text

    def test_entity_with_no_attributes_is_still_declared(self, schema):
        schema["Empty"] = {"version": 1, "fields": {}, "relationships": {}}
        text, _ = generate_cedar_schema(schema, "Realm", "User")
        assert "entity Empty;" in text

    def test_output_is_deterministic(self, schema):
        first, _ = generate_cedar_schema(schema, "Realm", "User")
        second, _ = generate_cedar_schema(schema, "Realm", "User")
        assert first == second

    def test_types_from_another_namespace_are_refused(self, schema):
        # ic-python-db writes extension-owned entities as `ext_foo::Bar`, which
        # is also Cedar's namespace separator. Emitting that inside `Realm`
        # would produce a name Cedar misparses, so it has to be an error.
        schema["ext_procurement::RFP"] = {
            "version": 1,
            "fields": {},
            "relationships": {},
        }
        with pytest.raises(ValueError, match="another namespace"):
            generate_cedar_schema(schema, "Realm", "User")

    def test_report_counts_what_was_emitted(self, schema):
        _, report = generate_cedar_schema(schema, "Realm", "User")
        assert report.entities == 2
        # User.id, Department.name/is_root/quorum/head
        assert report.attributes == 5


class TestExtensionSchema:
    """Extension-owned entities are declared in a manifest, not as classes."""

    MANIFEST = {
        "Rfp": {
            "alias": "rfp_id",
            "fields": {
                "rfp_id": {"type": "String", "max_length": 64},
                "closes_at": {"type": "Integer"},
                "total_score": {"type": "Float"},
            },
        },
    }

    def test_namespace_matches_the_storage_namespace(self):
        # ic-python-db stores these under `ext_<name>::Class`, so the Cedar type
        # and the storage key agree without a translation table.
        assert extension_namespace("procurement") == "ext_procurement"
        text, _ = generate_extension_schema("procurement", self.MANIFEST)
        assert text.startswith("namespace ext_procurement {")

    def test_manifest_fields_become_attributes(self):
        text, _ = generate_extension_schema("procurement", self.MANIFEST)
        assert "rfp_id?: String," in text
        assert "closes_at?: Long," in text

    def test_manifest_conversion_is_reusable_on_its_own(self):
        converted = manifest_to_schema(self.MANIFEST)
        assert converted["Rfp"]["fields"]["rfp_id"] == {
            "kind": "property",
            "type": "String",
        }
        assert converted["Rfp"]["relationships"] == {}

    def test_unrepresentable_fields_are_reported_the_same_way(
        self,
    ):
        _, report = generate_extension_schema("procurement", self.MANIFEST)
        assert ("Rfp", "total_score", "Cedar has no floating point type") in (
            report.skipped_fields
        )

    def test_actions_are_scoped_to_the_extensions_own_resources(self):
        # This is what contains an extension. Validated against this schema
        # alone, a policy naming anything outside the namespace has no
        # applicable action, so it fails to validate rather than needing a
        # reviewer to notice.
        text, _ = generate_extension_schema("procurement", self.MANIFEST)
        assert "resource: [Rfp]" in text
        assert "Realm::" not in text.replace("principal: [Realm::User]", "")

    def test_the_principal_is_the_realms_user_not_the_extensions(self):
        text, _ = generate_extension_schema("procurement", self.MANIFEST)
        assert "principal: [Realm::User]" in text

    def test_a_qualified_principal_need_not_be_declared_locally(self):
        # The principal lives in another namespace, which would otherwise trip
        # the "principal must be a known entity type" check.
        generate_extension_schema("procurement", self.MANIFEST)

    def test_an_unqualified_unknown_principal_is_still_refused(self):
        with pytest.raises(ValueError, match="principal type"):
            generate_extension_schema(
                "procurement", self.MANIFEST, principal_type="Ghost"
            )

    def test_an_extension_with_no_entities_still_generates(self):
        text, report = generate_extension_schema("frontend_only", {})
        assert "namespace ext_frontend_only {" in text
        assert report.entities == 0
