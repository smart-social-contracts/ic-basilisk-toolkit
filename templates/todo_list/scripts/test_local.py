#!/usr/bin/env python3
"""Smoke test the todo_list canister on the local replica (via icp)."""

import ast
import os
import re
import subprocess
import sys

CANISTER = "todo_list"
ENV = {**os.environ, "TERM": "xterm-256color"}


def run(cmd: list[str], *, check: bool = True) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, env=ENV)
    out = (proc.stdout or "") + (proc.stderr or "")
    if check and proc.returncode != 0:
        raise RuntimeError(out)
    return out


def icp_call(method: str, arg: str) -> str:
    return run(["icp", "canister", "call", CANISTER, method, arg])


def shell(code: str) -> str:
    escaped = code.replace("\\", "\\\\").replace('"', '\\"')
    return icp_call("__shell__", f'("{escaped}")')


def clean_icp_output(text: str) -> str:
    lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.startswith("WARN ")
    ]
    return "\n".join(lines)


def unwrap_candid(text: str) -> str:
    text = clean_icp_output(text)
    m = re.search(r'\(\s*"((?:\\.|[^"\\])*)"\s*,?\s*\)', text, re.DOTALL)
    if not m:
        return text.strip()
    return bytes(m.group(1), "utf-8").decode("unicode_escape")


def icp_identity_principals() -> dict[str, str]:
    out = run(["icp", "identity", "list"], check=False)
    mapping: dict[str, str] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("WARN"):
            continue
        if line.startswith("* "):
            line = line[2:].strip()
        parts = line.split()
        if len(parts) >= 2:
            mapping[parts[0]] = parts[1]
    return mapping


def icp_use(name: str) -> None:
    run(["icp", "identity", "default", name])


def icp_other_identity(current: str) -> str | None:
    principals = icp_identity_principals()
    current_principal = principals.get(current, current)
    for name, principal in principals.items():
        if name != current and principal != current_principal:
            return name
    return None


def parse_repr_dict(text: str) -> dict:
    body = unwrap_candid(text).strip()
    m = re.search(r"\{.*\}", body, re.DOTALL)
    if not m:
        raise ValueError(f"no dict in shell output: {body!r}")
    return ast.literal_eval(m.group(0))


def icp_whoami() -> str:
    out = run(["icp", "identity", "default"], check=False)
    for line in out.splitlines():
        line = line.strip()
        if line and not line.startswith("WARN") and not line.startswith("error:"):
            return line.split()[0]
    return "unknown"


def main() -> int:
    print("== status ==")
    print(clean_icp_output(icp_call("status", "()")))

    owner_identity = icp_whoami()
    print(f"== create list ({owner_identity}) ==")
    out = shell("lst = TodoList.create(title='groceries'); print(repr({'id': lst.id, 'title': lst.title}))")
    created = unwrap_candid(out)
    print(created)
    row = parse_repr_dict(out)
    list_id = row["id"]

    print("== list owned lists ==")
    print(unwrap_candid(shell("print(repr([{'id': x.id, 'title': x.title} for x in TodoList.mine()]))")))

    other = icp_other_identity(owner_identity)
    if other is None:
        print("SKIP cross-user test (need a second icp identity: icp identity new bob)")
    else:
        print(f"== {other} denied on update ==")
        icp_use(other)
        deny = shell(
            f"try:\n"
            f"    lst = TodoList.load('{list_id}')\n"
            f"    lst.title = 'hacked'\n"
            f"    print('unexpected success')\n"
            f"except Exception as e:\n"
            f"    print(repr(str(e)))"
        )
        deny_body = unwrap_candid(deny)
        print(deny_body)
        if "PermissionError" not in deny_body and "Cedar denied" not in deny_body:
            print("FAIL: expected Cedar denial")
            return 1
        print("PASS: cross-user update denied")
        icp_use(owner_identity)

    print("== add item ==")
    print(unwrap_candid(shell(
        f"lst = TodoList.load('{list_id}'); "
        f"item = TodoItem.create(title='milk', todo_list=lst); "
        f"print(repr({{'id': item.id, 'title': item.title}}))"
    )))
    print(unwrap_candid(shell(
        f"lst = TodoList.load('{list_id}'); "
        f"print(repr([{{'id': i.id, 'title': i.title, 'done': i.done}} for i in lst.items()]))"
    )))

    print(f"All smoke tests passed (list={list_id}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
