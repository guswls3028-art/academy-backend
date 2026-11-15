"""Read local coding-session metadata and optionally publish one private snapshot.

No model calls, conversation export, app-server control, or session mutations.
Run --once for a bounded inspection. --publish uses the existing AWS/R2 stores;
credentials are read only inside this process and are never printed or saved.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

SCHEMA = 1
MAX_NODES = 600
TAIL_BYTES = 2 * 1024 * 1024
OBJECT_KEY = "_platform/agent-sessions/admin97/latest.json"
AWS_ACCOUNT = "809466760795"
R2_ACCOUNT = "af4f2937d73db240e99864b8518265c5"
R2_BUCKET = "academy-storage"
R2_FIELDS = {"R2_ENDPOINT", "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_REGION", "R2_STORAGE_BUCKET"}
_TAIL_CACHE = {}


def iso(value=None):
    value = time.time() if value is None else value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()


def label(value, limit=160):
    value = value if isinstance(value, str) else ""
    value = re.sub(r"(?i)(?:sk-[a-z0-9_-]{12,}|bearer\s+\S+|(?:password|api[_ -]?key|secret|token)\s*[:=]\s*\S+)", "[비공개]", value)
    return " ".join(value.split())[:limit]


def within(path, root):
    try:
        relative = Path(str(path).removeprefix("\\\\?\\")).resolve().relative_to(Path(str(root).removeprefix("\\\\?\\")).resolve())
    except (ValueError, OSError):
        return False
    return not any(part.lower() in {"dnfm", "dnfm-group"} for part in relative.parts)


def metadata_tail(path, provider="claude"):
    """Keep only event enums and display-name metadata, never message bodies."""
    path = Path(str(path).removeprefix("\\\\?\\"))
    try:
        stat = path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns)
        cached = _TAIL_CACHE.get(str(path))
        if cached and cached[0] == stamp:
            if cached[1]["status"] == "running" and time.time() - stat.st_mtime > 1800:
                return {**cached[1], "status": "unknown"}
            return cached[1]
        with path.open("rb") as stream:
            offset = max(0, stat.st_size - TAIL_BYTES)
            stream.seek(offset)
            if offset:
                stream.readline()
            lines = stream.read(TAIL_BYTES).splitlines()
        result = {"status": "unknown", "title": "", "role": "", "updated_at": iso(stat.st_mtime)}
        for encoded in reversed(lines):
            if provider == "codex" and not any(event in encoded for event in (b'"task_started"', b'"task_complete"', b'"turn_aborted"')):
                continue
            try:
                item = json.loads(encoded)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            payload = item.get("payload") or {}
            if kind == "custom-title" and not result["title"]:
                result["title"] = label(item.get("customTitle"))
            if kind == "agent-name" and not result["role"]:
                result["role"] = label(item.get("agentName"), 80)
            if result["status"] != "unknown":
                continue
            if kind == "event_msg" and isinstance(payload, dict):
                state = {"task_started": "running", "task_complete": "completed", "turn_aborted": "interrupted"}.get(payload.get("type"))
                if state:
                    result["status"] = state
                    if isinstance(item.get("timestamp"), str):
                        result["updated_at"] = iso(item["timestamp"])
                    if provider == "codex":
                        break
            elif kind == "assistant" and isinstance(item.get("message"), dict):
                if item["message"].get("stop_reason") in {"end_turn", "stop_sequence"}:
                    result["status"] = "completed"
            elif kind == "system" and item.get("subtype") == "turn_duration":
                result["status"] = "completed"
        # A lost process must not look perpetually busy on the basis of an old log.
        if result["status"] == "running" and time.time() - stat.st_mtime > 1800:
            result["status"] = "unknown"
        _TAIL_CACHE[str(path)] = (stamp, result)
        if len(_TAIL_CACHE) > 1200:
            _TAIL_CACHE.clear()
        return result
    except (OSError, ValueError):
        return {"status": "unknown", "title": "", "role": "", "updated_at": None}


def codex_nodes(home, workspace):
    databases = sorted(Path(home).glob("state_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not databases:
        raise FileNotFoundError("Codex metadata unavailable")
    db = sqlite3.connect(databases[0].as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute("SELECT id, name, agent_nickname, agent_role, rollout_path, cwd, updated_at, archived FROM threads WHERE lower(cwd) LIKE '%academy%' AND lower(cwd) NOT LIKE '%dnfm%' ORDER BY updated_at DESC LIMIT 2500").fetchall()
        parents = defaultdict(set)
        for row in db.execute("SELECT e.parent_thread_id, e.child_thread_id FROM thread_spawn_edges e JOIN threads t ON t.id=e.child_thread_id WHERE lower(t.cwd) LIKE '%academy%' AND lower(t.cwd) NOT LIKE '%dnfm%'"):
            parents[row["child_thread_id"]].add(row["parent_thread_id"])
    finally:
        db.close()
    scoped = [row for row in rows if within(row["cwd"], workspace)]
    # Recent roots plus their actual descendants; no inferred manager hierarchy.
    roots = {row["id"] for row in scoped if not parents[row["id"]]}
    selected_roots = set(list(dict.fromkeys(row["id"] for row in scoped if row["id"] in roots))[:100])
    allowed = set(selected_roots)
    for _ in range(32):
        descendants = {row["id"] for row in scoped if any(parent in allowed for parent in parents[row["id"]])}
        if descendants <= allowed:
            break
        allowed.update(descendants)
    result = []
    for row in scoped:
        if row["id"] not in allowed:
            continue
        path = row["rollout_path"]
        # Database metadata is not authority to read arbitrary local files.
        safe_path = within(path, Path(home) / "sessions") or within(path, Path(home) / "archived_sessions")
        meta = metadata_tail(path, "codex") if safe_path else {"status": "unknown", "updated_at": None}
        parent_set = parents[row["id"]]
        parent = next(iter(parent_set)) if len(parent_set) == 1 else None
        status = meta["status"]
        if row["archived"] and status != "completed":
            status = "interrupted"
        result.append({
            "id": "codex:" + row["id"], "provider": "codex",
            "parent_id": "codex:" + parent if parent else None,
            # title/first_user_message/preview may contain the raw initial prompt.
            "title": label(row["name"] or row["agent_nickname"]) or "Codex " + row["id"][:8],
            "role": label(row["agent_role"], 80) or ("하위 에이전트" if parent else "세션"),
            "status": status, "evidence": "last_turn" if status != "unknown" else "unknown",
            "updated_at": meta["updated_at"] or iso(row["updated_at"]),
        })
    return result


def peer_is_alive(peer):
    """Verify Windows process identity without signals or touching its socket/key."""
    if os.name != "nt" or not isinstance(peer.get("pid"), int) or not peer.get("procStart"):
        return False
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, peer["pid"])
    if not handle:
        return False
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        code = wintypes.DWORD()
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)) or not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return code.value == 259 and str(created) == str(peer["procStart"])
    finally:
        kernel.CloseHandle(handle)


def claude_nodes(home, workspace):
    home, workspace = Path(home), Path(workspace).resolve()
    if not home.is_dir():
        raise FileNotFoundError("Claude metadata unavailable")
    result = {}
    # Only Academy transcript metadata; never scan other project conversations.
    for project in (home / "projects").glob("*"):
        expected = re.sub(r"[^A-Za-z0-9]", "-", str(workspace))
        if project.name.casefold() != expected.casefold() or not within(project, home / "projects"):
            continue
        logs = sorted(project.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:80]
        for log in logs:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", log.stem) or not within(log, project):
                continue
            meta = metadata_tail(log)
            node_id = "claude:" + log.stem
            result[node_id] = {"id": node_id, "provider": "claude", "parent_id": None,
                               "title": meta["title"] or "Claude " + log.stem[:8], "role": "세션",
                               "status": meta["status"], "evidence": "last_turn" if meta["status"] != "unknown" else "unknown",
                               "updated_at": meta["updated_at"]}
            for child in (project / log.stem / "subagents").glob("agent-*.jsonl"):
                if not within(child, project) or not re.fullmatch(r"agent-[A-Za-z0-9_-]{1,50}", child.stem):
                    continue
                child_meta = metadata_tail(child)
                child_id = node_id + "_" + child.stem.removeprefix("agent-")
                result[child_id] = {"id": child_id, "provider": "claude", "parent_id": node_id,
                                    "title": child_meta["title"] or "에이전트 " + child.stem[6:14],
                                    "role": child_meta["role"] or "하위 에이전트",
                                    "status": child_meta["status"], "evidence": "last_turn" if child_meta["status"] != "unknown" else "unknown",
                                    "updated_at": child_meta["updated_at"]}
    for path in (home / "sessions").glob("*.json"):
        if not within(path, home / "sessions") or path.stat().st_size > 32_768:
            continue
        try:
            peer = json.loads(path.read_bytes())
            cwd = Path(peer.get("cwd", "")).resolve()
            # The CLI may be launched at the drive root without a project yet.
            if not within(cwd, workspace) and cwd != Path(workspace.anchor):
                continue
            session_id = peer.get("sessionId", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id):
                continue
            node_id = "claude:" + session_id
            fresh = peer_is_alive(peer)
            status = {"idle": "waiting", "working": "running", "running": "running", "waiting_for_input": "waiting", "needs_attention": "waiting"}.get(peer.get("status"), "unknown") if fresh else "unknown"
            result[node_id] = {"id": node_id, "provider": "claude", "parent_id": None,
                               "title": label(peer.get("name")) or result.get(node_id, {}).get("title") or "Claude " + session_id[:8],
                               "role": "세션" if within(cwd, workspace) else "프로젝트 미지정",
                               "status": status, "evidence": "runtime" if fresh else "unknown",
                               "updated_at": iso(peer["updatedAt"] / 1000) if isinstance(peer.get("updatedAt"), (int, float)) else iso(path.stat().st_mtime)}
        except (OSError, ValueError, TypeError):
            continue
    return list(result.values())


def collect(codex_home, claude_home, workspace):
    nodes, source_errors = [], []
    for provider, reader, home in (("codex", codex_nodes, codex_home), ("claude", claude_nodes, claude_home)):
        try:
            nodes.extend(reader(home, workspace))
        except (OSError, ValueError, sqlite3.Error):
            source_errors.append(provider)
    nodes.sort(key=lambda node: node["updated_at"] or "", reverse=True)
    # Include parents ahead of children, keeping the retained tree understandable.
    by_id = {node["id"]: node for node in nodes}
    chosen = {}

    def include(node_id, trail=()):
        if node_id in chosen or node_id in trail or len(trail) > 32 or node_id not in by_id:
            return
        node = by_id[node_id]
        include(node["parent_id"], (*trail, node_id))
        if len(chosen) < MAX_NODES:
            chosen[node_id] = node

    for node in nodes:
        include(node["id"])
    return {"schema": SCHEMA, "collected_at": iso(), "nodes": list(chosen.values()), "truncated": len(chosen) < len(nodes), "source_errors": source_errors}


def publishing_client():
    import boto3
    from botocore.config import Config

    session = boto3.Session(region_name="ap-northeast-2")
    bounded = Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1})
    if session.client("sts", config=bounded).get_caller_identity()["Account"] != AWS_ACCOUNT:
        raise ValueError("Wrong AWS account")
    encoded = session.client("ssm", config=bounded).get_parameter(Name="/academy/api/env", WithDecryption=True)["Parameter"]["Value"]
    if encoded.lstrip().startswith("{"):
        values = json.loads(encoded)
    else:
        values = {}
        for line in encoded.splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in R2_FIELDS:
                values[key.strip()] = value.strip().strip('"').strip("'")
    config = {key: values.get(key, "") for key in R2_FIELDS}
    del encoded, values
    if config["R2_STORAGE_BUCKET"] != R2_BUCKET or config["R2_ENDPOINT"].rstrip("/") != f"https://{R2_ACCOUNT}.r2.cloudflarestorage.com":
        raise ValueError("Unexpected private storage target")
    if not config["R2_ACCESS_KEY"] or not config["R2_SECRET_KEY"]:
        raise ValueError("Missing storage credentials")
    return boto3.client("s3", endpoint_url=config["R2_ENDPOINT"], aws_access_key_id=config["R2_ACCESS_KEY"], aws_secret_access_key=config["R2_SECRET_KEY"], region_name=config["R2_REGION"] or "auto", config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1}))


def publisher_lock():
    """Hold one OS-owned publisher lock; crashes automatically release it."""
    if os.name != "nt":
        raise OSError("The installed publisher requires Windows")
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel.CreateMutexW(None, True, "Local\\AcademyAgentSessions-admin97")
    if not handle or ctypes.get_last_error() == 183:
        if handle:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(handle)
        raise OSError("Publisher already running or unavailable")
    return handle  # Keep the handle alive until this process exits.


def report(state, path=None):
    """Atomic, metadata-free health receipt also works with pythonw.exe."""
    encoded = json.dumps({**state, "checked_at": iso(), "pid": os.getpid()})
    if path:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)
    if sys.stdout is not None:
        print(encoded, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--claude-home", type=Path, default=Path.home() / ".claude")
    parser.add_argument("--workspace", type=Path, default=Path("C:/academy"))
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--output", type=Path, help="Optional local metadata snapshot; never a Git path")
    parser.add_argument("--state-path", type=Path, help="Sanitized publisher health receipt")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    if args.interval < 10:
        parser.error("interval must be at least 10 seconds")
    if args.publish:
        try:
            publisher_lock()
        except OSError:
            # Do not overwrite a running publisher's receipt.
            return 2
    client, client_created = None, 0
    while True:
        try:
            snapshot = collect(args.codex_home, args.claude_home, args.workspace)
            encoded = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
            if len(encoded) > 1024 * 1024:
                raise ValueError("Snapshot exceeds size limit")
            if args.output:
                args.output.write_bytes(encoded)
            if args.publish:
                if client is None or time.monotonic() - client_created > 3600:
                    client = publishing_client()
                    client_created = time.monotonic()
                client.put_object(Bucket=R2_BUCKET, Key=OBJECT_KEY, Body=encoded, ContentType="application/json", CacheControl="no-store")
            report({"status": "published" if args.publish else "inspected", "nodes": len(snapshot["nodes"]), "source_errors": snapshot["source_errors"], "truncated": snapshot["truncated"], "at": snapshot["collected_at"]}, args.state_path)
        except Exception as exc:
            # SDK errors may contain secrets; no raw exception text or traceback.
            report({"status": "unavailable", "error_class": type(exc).__name__}, args.state_path)
            client = None
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
