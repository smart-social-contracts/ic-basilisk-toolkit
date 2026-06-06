"""
Unit tests for ic-file-registry pure helper functions.

These tests mock basilisk imports so no canister or icp is needed.
Run with: pytest tests/test_unit.py -v
"""

import base64
import hashlib
import json
import os
import sys
import tempfile
import unittest


# ---------------------------------------------------------------------------
# Mock basilisk before importing src/main.py
# ---------------------------------------------------------------------------

class _FakeRecord:
    pass

class _Subscriptable:
    """Fake generic type that supports [] syntax in class bodies."""
    def __class_getitem__(cls, item):
        return cls

class _FakePrincipal:
    @staticmethod
    def from_str(s):
        return s

_mock_ic = type("ic_obj", (), {
    "caller": lambda self: type("p", (), {"to_str": lambda s: "aaaaa-aa"})(),
    "is_controller": lambda self, p: False,
    "time": lambda self: 1_700_000_000_000_000_000,
    "id": lambda self: type("p", (), {"to_str": lambda s: "aaaaa-aa"})(),
})()

_mock_basilisk = type(sys)("basilisk")
_mock_basilisk.blob = bytes
_mock_basilisk.ic = _mock_ic
_mock_basilisk.nat16 = int
_mock_basilisk.Opt = _Subscriptable
_mock_basilisk.Principal = _FakePrincipal
_mock_basilisk.query = lambda f: f
_mock_basilisk.Record = _FakeRecord
_mock_basilisk.text = str
_mock_basilisk.update = lambda f: f
_mock_basilisk.Vec = _Subscriptable

sys.modules["basilisk"] = _mock_basilisk


# ---------------------------------------------------------------------------
# Load the module under test
# ---------------------------------------------------------------------------

import importlib.util

_SRC = os.path.join(os.path.dirname(__file__), "..", "src", "main.py")
_spec = importlib.util.spec_from_file_location("registry_main", _SRC)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Re-export for convenience
_guess_content_type = _mod._guess_content_type
_file_path = _mod._file_path
_meta_path = _mod._meta_path
_chunk_file_path = _mod._chunk_file_path
_pending_meta_path = _mod._pending_meta_path
_http_response = _mod._http_response
_load_namespaces = _mod._load_namespaces
_save_namespaces = _mod._save_namespaces
_load_meta = _mod._load_meta
_save_meta = _mod._save_meta
_load_acl = _mod._load_acl
_save_acl = _mod._save_acl
REGISTRY_DIR = _mod.REGISTRY_DIR
CHUNKS_DIR = _mod.CHUNKS_DIR


# ---------------------------------------------------------------------------
# Test: _guess_content_type
# ---------------------------------------------------------------------------

class TestGuessContentType(unittest.TestCase):

    def test_python(self):
        self.assertEqual(_guess_content_type("entry.py"), "text/plain")

    def test_javascript(self):
        self.assertEqual(_guess_content_type("main.js"), "application/javascript")
        self.assertEqual(_guess_content_type("module.mjs"), "application/javascript")

    def test_json(self):
        self.assertEqual(_guess_content_type("manifest.json"), "application/json")

    def test_wasm(self):
        self.assertEqual(_guess_content_type("canister.wasm"), "application/wasm")

    def test_html(self):
        self.assertEqual(_guess_content_type("index.html"), "text/html")

    def test_css(self):
        self.assertEqual(_guess_content_type("styles.css"), "text/css")

    def test_markdown(self):
        self.assertEqual(_guess_content_type("README.md"), "text/markdown")

    def test_svg(self):
        self.assertEqual(_guess_content_type("icon.svg"), "image/svg+xml")

    def test_png(self):
        self.assertEqual(_guess_content_type("logo.png"), "image/png")

    def test_jpeg(self):
        self.assertEqual(_guess_content_type("photo.jpg"), "image/jpeg")
        self.assertEqual(_guess_content_type("photo.jpeg"), "image/jpeg")

    def test_yaml(self):
        self.assertEqual(_guess_content_type("config.yaml"), "text/plain")
        self.assertEqual(_guess_content_type("config.yml"), "text/plain")

    def test_toml(self):
        self.assertEqual(_guess_content_type("Cargo.toml"), "text/plain")

    def test_unknown_extension(self):
        self.assertEqual(_guess_content_type("binary.xyz"), "application/octet-stream")

    def test_no_extension(self):
        self.assertEqual(_guess_content_type("Makefile"), "application/octet-stream")

    def test_nested_path(self):
        self.assertEqual(_guess_content_type("deep/nested/entry.py"), "text/plain")


# ---------------------------------------------------------------------------
# Test: path construction helpers
# ---------------------------------------------------------------------------

class TestPathHelpers(unittest.TestCase):

    def test_file_path_simple(self):
        result = _file_path("extensions", "entry.py")
        self.assertEqual(result, f"{REGISTRY_DIR}/extensions/entry.py")

    def test_file_path_strips_leading_slash(self):
        result = _file_path("extensions", "/entry.py")
        self.assertEqual(result, f"{REGISTRY_DIR}/extensions/entry.py")

    def test_file_path_nested(self):
        result = _file_path("extensions", "hello_world/v0.1/entry.py")
        self.assertEqual(result, f"{REGISTRY_DIR}/extensions/hello_world/v0.1/entry.py")

    def test_meta_path(self):
        result = _meta_path("extensions")
        self.assertEqual(result, f"{REGISTRY_DIR}/extensions/_meta.json")

    def test_chunk_file_path(self):
        result = _chunk_file_path("extensions", "hello_world/entry.py", 0)
        self.assertIn(CHUNKS_DIR, result)
        self.assertIn("extensions", result)
        self.assertIn("0000", result)

    def test_chunk_file_path_index_padding(self):
        p0 = _chunk_file_path("ns", "file.bin", 0)
        p9 = _chunk_file_path("ns", "file.bin", 9)
        p99 = _chunk_file_path("ns", "file.bin", 99)
        self.assertIn("0000", p0)
        self.assertIn("0009", p9)
        self.assertIn("0099", p99)

    def test_pending_meta_path(self):
        result = _pending_meta_path("extensions", "hello_world/entry.py")
        self.assertIn(CHUNKS_DIR, result)
        self.assertIn("pending.json", result)


# ---------------------------------------------------------------------------
# Test: JSON file I/O helpers (using temp directory)
# ---------------------------------------------------------------------------

class TestNamespacesIO(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_ns_file = _mod.NAMESPACES_FILE
        self._orig_reg_dir = _mod.REGISTRY_DIR
        self._orig_chunks_dir = _mod.CHUNKS_DIR
        _mod.NAMESPACES_FILE = os.path.join(self.tmpdir, "_namespaces.json")
        _mod.REGISTRY_DIR = self.tmpdir
        _mod.CHUNKS_DIR = os.path.join(self.tmpdir, "_chunks")

    def tearDown(self):
        _mod.NAMESPACES_FILE = self._orig_ns_file
        _mod.REGISTRY_DIR = self._orig_reg_dir
        _mod.CHUNKS_DIR = self._orig_chunks_dir

    def test_load_missing_file_returns_empty(self):
        result = _load_namespaces()
        self.assertEqual(result, {})

    def test_save_and_load(self):
        data = {"extensions": {"namespace": "extensions", "created": 0, "owner": "x", "description": ""}}
        _save_namespaces(data)
        result = _load_namespaces()
        self.assertEqual(result, data)

    def test_corrupted_file_returns_empty(self):
        path = os.path.join(self.tmpdir, "_namespaces.json")
        with open(path, "w") as f:
            f.write("not valid json {{{")
        result = _load_namespaces()
        self.assertEqual(result, {})


class TestMetaIO(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_reg_dir = _mod.REGISTRY_DIR
        _mod.REGISTRY_DIR = self.tmpdir

    def tearDown(self):
        _mod.REGISTRY_DIR = self._orig_reg_dir

    def test_load_missing_meta_returns_empty(self):
        result = _load_meta("nonexistent_namespace")
        self.assertEqual(result, {"files": {}})

    def test_save_and_load_meta(self):
        meta = {
            "files": {
                "entry.py": {"size": 100, "content_type": "text/plain", "sha256": "abc", "updated": 0}
            }
        }
        _save_meta("extensions", meta)
        result = _load_meta("extensions")
        self.assertEqual(result, meta)

    def test_meta_creates_namespace_dir(self):
        _save_meta("new_ns", {"files": {}})
        ns_dir = os.path.join(self.tmpdir, "new_ns")
        self.assertTrue(os.path.isdir(ns_dir))


class TestAclIO(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_acl_file = _mod.ACL_FILE
        self._orig_reg_dir = _mod.REGISTRY_DIR
        self._orig_chunks_dir = _mod.CHUNKS_DIR
        _mod.ACL_FILE = os.path.join(self.tmpdir, "_acl.json")
        _mod.REGISTRY_DIR = self.tmpdir
        _mod.CHUNKS_DIR = os.path.join(self.tmpdir, "_chunks")

    def tearDown(self):
        _mod.ACL_FILE = self._orig_acl_file
        _mod.REGISTRY_DIR = self._orig_reg_dir
        _mod.CHUNKS_DIR = self._orig_chunks_dir

    def test_load_missing_acl_returns_empty(self):
        result = _load_acl()
        self.assertEqual(result, {})

    def test_save_and_load_acl(self):
        acl = {"extensions": ["principal-abc", "principal-xyz"]}
        _save_acl(acl)
        result = _load_acl()
        self.assertEqual(result, acl)


# ---------------------------------------------------------------------------
# Test: _http_response helper
# ---------------------------------------------------------------------------

class TestHttpResponse(unittest.TestCase):

    def test_basic_structure(self):
        body = b'{"test": true}'
        resp = _http_response(200, body, "application/json")
        self.assertEqual(resp["status_code"], 200)
        self.assertEqual(resp["body"], body)
        self.assertIsNone(resp["streaming_strategy"])
        self.assertIsNone(resp["upgrade"])

    def test_cors_header_present(self):
        resp = _http_response(200, b"ok", "text/plain")
        header_names = [h[0] for h in resp["headers"]]
        self.assertIn("Access-Control-Allow-Origin", header_names)

    def test_content_type_header(self):
        resp = _http_response(200, b"ok", "application/wasm")
        headers = dict(resp["headers"])
        self.assertEqual(headers["Content-Type"], "application/wasm")

    def test_content_length(self):
        body = b"hello world"
        resp = _http_response(200, body, "text/plain")
        headers = dict(resp["headers"])
        self.assertEqual(headers["Content-Length"], str(len(body)))

    def test_extra_headers_appended(self):
        resp = _http_response(200, b"", "text/plain", [("Cache-Control", "max-age=3600")])
        headers = dict(resp["headers"])
        self.assertEqual(headers["Cache-Control"], "max-age=3600")

    def test_404_status(self):
        body = b'{"error": "not found"}'
        resp = _http_response(404, body, "application/json")
        self.assertEqual(resp["status_code"], 404)

    def test_empty_body(self):
        resp = _http_response(204, b"", "text/plain")
        headers = dict(resp["headers"])
        self.assertEqual(headers["Content-Length"], "0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
