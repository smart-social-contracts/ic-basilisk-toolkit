#!/usr/bin/env python3
"""
Upload a file (or directory of files) to the ic-file-registry canister.

Usage:
    python3 scripts/upload_file.py \\
        --canister <CANISTER_ID> \\
        --namespace extensions/hello_world \\
        --path backend/entry.py \\
        --file ./extensions/hello_world/backend/entry.py \\
        [--network ic|local]

    # Upload entire directory:
    python3 scripts/upload_file.py \\
        --canister <CANISTER_ID> \\
        --namespace extensions/hello_world \\
        --dir ./extensions/hello_world/ \\
        [--network ic|local]
"""

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys

CHUNK_SIZE = 500_000  # 500 KB per chunk (safe below 2 MB message limit)


def icp_call(canister_id: str, method: str, arg: str, network: str = "local") -> str:
    cmd = [
        "icp", "canister", "call",
        # canister_id is a principal, so target it by network (-n).
        "-n", network,
        canister_id, method,
        f'("{arg}")',
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    # Strip Candid wrapper: ("...") -> ...
    out = result.stdout.strip()
    if out.startswith('("') and out.endswith('")'):
        out = out[2:-2].replace('\\"', '"').replace("\\n", "\n")
    return out


def upload_single(canister_id: str, namespace: str, path: str,
                  file_bytes: bytes, content_type: str, network: str):
    total_size = len(file_bytes)
    print(f"  {namespace}/{path}  ({total_size:,} bytes, {content_type})", end=" ")

    if total_size <= CHUNK_SIZE:
        payload = json.dumps({
            "namespace": namespace,
            "path": path,
            "content_b64": base64.b64encode(file_bytes).decode("ascii"),
            "content_type": content_type,
        })
        raw = icp_call(canister_id, "store_file", payload, network)
        result = json.loads(raw)
        if "error" in result:
            print(f"FAILED: {result['error']}")
            return False
        print(f"OK  sha256={result['sha256'][:12]}...")
        return True
    else:
        # Chunked upload
        num_chunks = (total_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        print(f"[chunked: {num_chunks} chunks]")
        for i in range(num_chunks):
            chunk = file_bytes[i * CHUNK_SIZE:(i + 1) * CHUNK_SIZE]
            payload = json.dumps({
                "namespace": namespace,
                "path": path,
                "chunk_index": i,
                "total_chunks": num_chunks,
                "data_b64": base64.b64encode(chunk).decode("ascii"),
                "content_type": content_type,
            })
            raw = icp_call(canister_id, "store_file_chunk", payload, network)
            result = json.loads(raw)
            if "error" in result:
                print(f"  chunk {i}: FAILED: {result['error']}")
                return False
            print(f"  chunk {i + 1}/{num_chunks} OK")

        # Finalize
        payload = json.dumps({"namespace": namespace, "path": path})
        raw = icp_call(canister_id, "finalize_chunked_file", payload, network)
        result = json.loads(raw)
        if "error" in result:
            print(f"  finalize FAILED: {result['error']}")
            return False
        print(f"  finalize OK  sha256={result['sha256'][:12]}...  size={result['size']:,}")
        return True


CONTENT_TYPE_MAP = {
    ".py": "text/plain",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".json": "application/json",
    ".html": "text/html",
    ".css": "text/css",
    ".wasm": "application/wasm",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".svg": "image/svg+xml",
    ".ts": "text/plain",
    ".toml": "text/plain",
    ".yaml": "text/plain",
    ".yml": "text/plain",
}


def guess_content_type(path: str) -> str:
    for ext, ct in CONTENT_TYPE_MAP.items():
        if path.endswith(ext):
            return ct
    return "application/octet-stream"


def main():
    parser = argparse.ArgumentParser(description="Upload files to ic-file-registry")
    parser.add_argument("--canister", required=True, help="Canister ID or name")
    parser.add_argument("--namespace", required=True, help="Namespace (e.g. extensions/hello_world)")
    parser.add_argument("--network", default="local", help="Network: ic or local")
    parser.add_argument("--path", help="Logical path in registry (for single file upload)")
    parser.add_argument("--file", help="Local file to upload")
    parser.add_argument("--dir", help="Local directory to upload recursively")
    parser.add_argument("--content-type", help="Override content type")
    args = parser.parse_args()

    if args.dir:
        base_dir = args.dir.rstrip("/")
        uploaded = 0
        failed = 0
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in sorted(files):
                if fname.startswith(".") or fname.endswith(".pyc"):
                    continue
                local_path = os.path.join(root, fname)
                rel_path = os.path.relpath(local_path, base_dir)
                with open(local_path, "rb") as f:
                    content = f.read()
                ct = args.content_type or guess_content_type(fname)
                ok = upload_single(args.canister, args.namespace, rel_path, content, ct, args.network)
                if ok:
                    uploaded += 1
                else:
                    failed += 1
        print(f"\nDone: {uploaded} uploaded, {failed} failed")
    elif args.file:
        if not args.path:
            args.path = os.path.basename(args.file)
        with open(args.file, "rb") as f:
            content = f.read()
        ct = args.content_type or guess_content_type(args.file)
        ok = upload_single(args.canister, args.namespace, args.path, content, ct, args.network)
        sys.exit(0 if ok else 1)
    else:
        parser.error("Either --file or --dir is required")


if __name__ == "__main__":
    main()
