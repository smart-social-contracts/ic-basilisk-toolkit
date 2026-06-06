"""
Integration tests for ic-file-registry canister.

Requires a running local icp replica and the canister deployed.
The `canister` fixture (conftest.py) handles both automatically.

Run with:
    pytest tests/test_integration.py -v
"""

import base64
import hashlib
import json

import pytest

from .conftest import call_canister


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _store(namespace, path, content: bytes, content_type=None):
    payload = {"namespace": namespace, "path": path, "content_b64": _b64(content)}
    if content_type:
        payload["content_type"] = content_type
    return call_canister("store_file", json.dumps(payload), update=True)


def _delete_file(namespace, path):
    return call_canister("delete_file", json.dumps({"namespace": namespace, "path": path}), update=True)


def _delete_ns(namespace):
    return call_canister("delete_namespace", json.dumps({"namespace": namespace}), update=True)


# ---------------------------------------------------------------------------
# Basic state tests
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_namespaces_empty(self, canister):
        result = call_canister("list_namespaces")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_stats_zero(self, canister):
        result = call_canister("get_stats")
        assert result["namespaces"] == 0
        assert result["total_files"] == 0
        assert result["total_bytes"] == 0

    def test_acl_empty(self, canister):
        result = call_canister("get_acl")
        assert isinstance(result, dict)

    def test_list_files_unknown_namespace(self, canister):
        result = call_canister("list_files", json.dumps({"namespace": "nonexistent"}))
        assert isinstance(result, list)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# File store / get round-trip
# ---------------------------------------------------------------------------

class TestStoreAndGet:
    def test_store_small_text_file(self, canister):
        content = b"def greet(name):\n    return f'Hello, {name}!'\n"
        result = _store("test_ns", "entry.py", content)
        assert result.get("ok") is True
        assert result["namespace"] == "test_ns"
        assert result["path"] == "entry.py"
        assert result["size"] == len(content)
        assert result["sha256"] == hashlib.sha256(content).hexdigest()
        _delete_ns("test_ns")

    def test_get_file_roundtrip(self, canister):
        content = b"hello world"
        _store("test_get", "hello.txt", content, "text/plain")
        result = call_canister("get_file", json.dumps({"namespace": "test_get", "path": "hello.txt"}))
        assert "error" not in result
        recovered = base64.b64decode(result["content_b64"])
        assert recovered == content
        assert result["content_type"] == "text/plain"
        assert result["size"] == len(content)
        assert result["sha256"] == hashlib.sha256(content).hexdigest()
        _delete_ns("test_get")

    def test_get_nonexistent_file(self, canister):
        result = call_canister("get_file", json.dumps({"namespace": "test_get2", "path": "missing.py"}))
        assert "error" in result

    def test_leading_slash_stripped_from_path(self, canister):
        content = b"data"
        _store("test_slash", "/leading/slash.txt", content)
        result = call_canister("get_file", json.dumps({"namespace": "test_slash", "path": "leading/slash.txt"}))
        assert "error" not in result
        assert base64.b64decode(result["content_b64"]) == content
        _delete_ns("test_slash")

    def test_binary_file_roundtrip(self, canister):
        content = bytes(range(256)) * 10
        _store("test_binary", "data.bin", content, "application/octet-stream")
        result = call_canister("get_file", json.dumps({"namespace": "test_binary", "path": "data.bin"}))
        assert base64.b64decode(result["content_b64"]) == content
        _delete_ns("test_binary")

    def test_wasm_content_type_inferred(self, canister):
        content = b"\x00asm\x01\x00\x00\x00"
        _store("test_ct", "module.wasm", content)
        result = call_canister("get_file", json.dumps({"namespace": "test_ct", "path": "module.wasm"}))
        assert result["content_type"] == "application/wasm"
        _delete_ns("test_ct")


# ---------------------------------------------------------------------------
# List files
# ---------------------------------------------------------------------------

class TestListFiles:
    def test_list_files_after_upload(self, canister):
        _store("list_ns", "a.py", b"# a")
        _store("list_ns", "b.js", b"// b")
        _store("list_ns", "c.json", b"{}")
        result = call_canister("list_files", json.dumps({"namespace": "list_ns"}))
        paths = [f["path"] for f in result]
        assert "a.py" in paths
        assert "b.js" in paths
        assert "c.json" in paths
        _delete_ns("list_ns")

    def test_list_files_metadata(self, canister):
        content = b"test content"
        _store("meta_ns", "file.txt", content, "text/plain")
        result = call_canister("list_files", json.dumps({"namespace": "meta_ns"}))
        assert len(result) == 1
        f = result[0]
        assert f["path"] == "file.txt"
        assert f["size"] == len(content)
        assert f["content_type"] == "text/plain"
        assert len(f["sha256"]) == 64
        _delete_ns("meta_ns")


# ---------------------------------------------------------------------------
# Namespace listing and stats
# ---------------------------------------------------------------------------

class TestNamespacesAndStats:
    def test_namespace_appears_after_upload(self, canister):
        _store("ns_check", "file.py", b"pass")
        namespaces = call_canister("list_namespaces")
        names = [n["namespace"] for n in namespaces]
        assert "ns_check" in names
        _delete_ns("ns_check")

    def test_stats_update_after_upload(self, canister):
        content = b"x" * 1000
        _store("stats_ns", "big.txt", content)
        stats = call_canister("get_stats")
        assert stats["total_files"] >= 1
        assert stats["total_bytes"] >= 1000
        _delete_ns("stats_ns")

    def test_namespace_file_count(self, canister):
        _store("count_ns", "f1.py", b"1")
        _store("count_ns", "f2.py", b"2")
        _store("count_ns", "f3.py", b"3")
        namespaces = call_canister("list_namespaces")
        ns = next(n for n in namespaces if n["namespace"] == "count_ns")
        assert ns["file_count"] == 3
        _delete_ns("count_ns")


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_existing_file(self, canister):
        _store("del_ns", "to_delete.txt", b"bye")
        result = _delete_file("del_ns", "to_delete.txt")
        assert result.get("ok") is True
        gone = call_canister("get_file", json.dumps({"namespace": "del_ns", "path": "to_delete.txt"}))
        assert "error" in gone
        _delete_ns("del_ns")

    def test_delete_nonexistent_file(self, canister):
        result = _delete_file("del_ns2", "ghost.txt")
        assert "error" in result

    def test_delete_namespace(self, canister):
        _store("tmp_ns", "a.txt", b"a")
        _store("tmp_ns", "b.txt", b"b")
        result = _delete_ns("tmp_ns")
        assert result.get("ok") is True
        namespaces = call_canister("list_namespaces")
        names = [n["namespace"] for n in namespaces]
        assert "tmp_ns" not in names

    def test_file_count_decrements_after_delete(self, canister):
        _store("dec_ns", "keep.py", b"keep")
        _store("dec_ns", "drop.py", b"drop")
        _delete_file("dec_ns", "drop.py")
        files = call_canister("list_files", json.dumps({"namespace": "dec_ns"}))
        paths = [f["path"] for f in files]
        assert "keep.py" in paths
        assert "drop.py" not in paths
        _delete_ns("dec_ns")


# ---------------------------------------------------------------------------
# Chunked upload
# ---------------------------------------------------------------------------

class TestChunkedUpload:
    def test_chunked_upload_reassembles_correctly(self, canister):
        content = b"A" * 600_000 + b"B" * 600_000  # 1.2 MB, 3 chunks of 400KB
        chunk_size = 400_000
        chunks = [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)]
        total = len(chunks)
        namespace = "chunk_ns"
        path = "large_file.bin"

        for i, chunk in enumerate(chunks):
            result = call_canister("store_file_chunk", json.dumps({
                "namespace": namespace,
                "path": path,
                "chunk_index": i,
                "total_chunks": total,
                "data_b64": _b64(chunk),
                "content_type": "application/octet-stream",
            }), update=True)
            assert result.get("ok") is True
            assert result["uploaded"] == i + 1

        final = call_canister("finalize_chunked_file",
                              json.dumps({"namespace": namespace, "path": path}),
                              update=True)
        assert final.get("ok") is True
        assert final["size"] == len(content)
        assert final["sha256"] == hashlib.sha256(content).hexdigest()

        # Verify content
        got = call_canister("get_file", json.dumps({"namespace": namespace, "path": path}))
        assert base64.b64decode(got["content_b64"]) == content
        _delete_ns(namespace)

    def test_finalize_missing_chunk_returns_error(self, canister):
        result = call_canister("finalize_chunked_file",
                               json.dumps({"namespace": "phantom_ns", "path": "missing.bin"}),
                               update=True)
        assert "error" in result


# ---------------------------------------------------------------------------
# HTTP serving
# ---------------------------------------------------------------------------

class TestHttpRequest:
    def _http_get(self, path):
        req = json.dumps({
            "method": "GET",
            "url": f"/{path}",
            "headers": [],
            "body": "",
        })
        return call_canister("http_request", req)

    def test_root_returns_namespaces_json(self, canister):
        result = self._http_get("")
        assert result["status_code"] == 200
        cors = dict(result["headers"]).get("Access-Control-Allow-Origin")
        assert cors == "*"

    def test_file_served_correctly(self, canister):
        content = b'console.log("hello");'
        _store("http_ns", "main.js", content, "application/javascript")
        result = self._http_get("http_ns/main.js")
        assert result["status_code"] == 200
        headers = dict(result["headers"])
        assert headers.get("Content-Type") == "application/javascript"
        assert headers.get("Access-Control-Allow-Origin") == "*"
        _delete_ns("http_ns")

    def test_missing_file_returns_404(self, canister):
        result = self._http_get("http_ns2/doesnotexist.py")
        assert result["status_code"] == 404

    def test_options_preflight(self, canister):
        req = json.dumps({
            "method": "OPTIONS",
            "url": "/any/path",
            "headers": [],
            "body": "",
        })
        result = call_canister("http_request", req)
        assert result["status_code"] == 204
