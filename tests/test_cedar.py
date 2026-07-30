"""Unit tests for ic_basilisk_toolkit.cedar — the wrapper over _basilisk_cedar.

The native module exists only inside a canister built on the Cedar template, so
these substitute a fake one. What is under test is the translation layer: JSON
envelopes in, Python values or exceptions out.

Run: pytest tests/test_cedar.py -v
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ic_basilisk_toolkit import cedar


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
def fake(monkeypatch):
    def install(reply):
        module = FakeModule(reply)
        monkeypatch.setattr(cedar, "_module", lambda: module)
        return module

    return install


class TestMissingModule:
    def test_absent_native_module_is_a_clear_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "_basilisk_cedar", None)
        with pytest.raises(cedar.CedarError, match="Cedar template artifact"):
            cedar.load("schema", "policies")


class TestErrorsAreNotDenials:
    def test_error_envelope_raises_rather_than_denying(self, fake):
        # The whole point of the envelope: a failure must not read as a refusal.
        fake({"error": "entities: unknown attribute 'ghost'"})
        with pytest.raises(cedar.CedarError, match="unknown attribute"):
            cedar.is_authorized("p", "a", "r", [])

    def test_validation_failure_surfaces_from_load(self, fake):
        fake({"error": "validation: unrecognized entity type"})
        with pytest.raises(cedar.CedarError, match="unrecognized entity type"):
            cedar.load("schema", "policies")

    def test_malformed_reply_is_an_error(self, fake):
        fake("not json at all")
        with pytest.raises(cedar.CedarError, match="malformed reply"):
            cedar.is_authorized("p", "a", "r", [])

    def test_unexpected_reply_shape_is_an_error(self, fake):
        fake("[1, 2, 3]")
        with pytest.raises(cedar.CedarError, match="unexpected reply"):
            cedar.is_authorized("p", "a", "r", [])


class TestDecisions:
    def test_allow_is_true(self, fake):
        fake({"decision": "allow"})
        assert cedar.is_authorized("p", "a", "r", []) is True

    def test_deny_is_false(self, fake):
        fake({"decision": "deny"})
        assert cedar.is_authorized("p", "a", "r", []) is False


class TestBreadthWarnings:
    """A policy can typecheck and still hand out everything the extension owns."""

    def test_a_clean_load_warns_about_nothing(self, fake):
        fake({"ok": True, "warnings": []})
        assert cedar.load("schema", "policies") == []

    def test_an_older_module_without_warnings_still_works(self, fake):
        fake({"ok": True})
        assert cedar.load("schema", "policies") == []

    def test_warnings_reach_the_caller(self, fake):
        fake({"ok": True, "warnings": ["policy0: blanket permit — grants every"]})
        assert "blanket permit" in cedar.load("schema", "policies")[0]

    def test_a_warning_does_not_raise(self, fake):
        # Warn, do not refuse: a blanket permit is correct for an extension whose
        # data is meant to be public, and nothing here can tell the difference.
        fake({"ok": True, "warnings": ["policy0: blanket permit"]})
        cedar.load("schema", "policies")

    def test_a_malformed_warnings_field_is_an_error(self, fake):
        fake({"ok": True, "warnings": "blanket permit"})
        with pytest.raises(cedar.CedarError, match="'warnings' list"):
            cedar.load("schema", "policies")


class TestArgumentEncoding:
    def test_python_objects_are_encoded_as_json(self, fake):
        module = fake({"decision": "deny"})
        cedar.is_authorized("p", "a", "r", [{"uid": 1}], {"extension": "procurement"})
        _, args = module.calls[0]
        assert args[3] == '[{"uid": 1}]'
        assert args[4] == '{"extension": "procurement"}'

    def test_pre_encoded_json_is_passed_through(self, fake):
        module = fake({"decision": "deny"})
        cedar.is_authorized("p", "a", "r", "[]", '{"extension":"x"}')
        _, args = module.calls[0]
        assert args[3] == "[]"
        assert args[4] == '{"extension":"x"}'

    def test_absent_context_is_empty_not_null(self, fake):
        # Host-originated calls legitimately have no context, and the native
        # module treats an empty string as an empty context.
        module = fake({"decision": "deny"})
        cedar.is_authorized("p", "a", "r", [])
        _, args = module.calls[0]
        assert args[4] == ""


class TestAuthorizeMany:
    def test_returns_the_allowed_subset(self, fake):
        fake({"allowed": ['Realm::Case::"1"']})
        assert cedar.authorize_many("p", "a", ["x", "y"], []) == ['Realm::Case::"1"']

    def test_empty_result_is_a_list_not_an_error(self, fake):
        fake({"allowed": []})
        assert cedar.authorize_many("p", "a", ["x"], []) == []

    def test_missing_allowed_key_is_an_error(self, fake):
        fake({"decision": "allow"})
        with pytest.raises(cedar.CedarError, match="expected an 'allowed' list"):
            cedar.authorize_many("p", "a", ["x"], [])

    def test_error_envelope_raises(self, fake):
        fake({"error": "no policies loaded; call load() first"})
        with pytest.raises(cedar.CedarError, match="no policies loaded"):
            cedar.authorize_many("p", "a", ["x"], [])
