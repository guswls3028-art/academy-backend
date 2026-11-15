"""Private, bounded metadata snapshot for the developer's read-only session board."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re

from django.conf import settings

SNAPSHOT_KEY = "_platform/agent-sessions/admin97/latest.json"
MAX_BYTES = 1024 * 1024
MAX_NODES = 600
STALE_SECONDS = 90
STATES = {"running", "waiting", "completed", "interrupted", "unknown"}
EVIDENCE = {"runtime", "last_turn", "closed", "unknown"}
NODE_ID = re.compile(r"^(codex|claude):[A-Za-z0-9_-]{1,180}$")


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Invalid timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def safe_label(value, limit=160):
    if not isinstance(value, str):
        raise ValueError("Invalid label")
    # Names only. Never return tokens inadvertently pasted into a session name.
    value = re.sub(r"(?i)(?:sk-[a-z0-9_-]{12,}|bearer\s+\S+|(?:password|api[_ -]?key|secret|token)\s*[:=]\s*\S+)", "[비공개]", value)
    return " ".join(value.split())[:limit]


def normalize_snapshot(raw, *, now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(raw, dict) or type(raw.get("schema")) is not int or raw["schema"] != 1:
        raise ValueError("Unsupported snapshot")
    errors = raw.get("source_errors", [])
    if not isinstance(errors, list) or any(source not in ("codex", "claude") for source in errors) or type(raw.get("truncated", False)) is not bool:
        raise ValueError("Invalid source status")
    collected = timestamp(raw.get("collected_at"))
    if (collected - now).total_seconds() > 60:
        raise ValueError("Snapshot clock is ahead")
    rows = raw.get("nodes")
    if not isinstance(rows, list) or len(rows) > MAX_NODES:
        raise ValueError("Snapshot is too large")
    nodes, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid node")
        node_id = row.get("id")
        if not isinstance(node_id, str) or not NODE_ID.fullmatch(node_id) or node_id in seen:
            raise ValueError("Invalid or duplicate node")
        provider = row.get("provider")
        if provider not in {"codex", "claude"} or not node_id.startswith(provider + ":"):
            raise ValueError("Invalid provider")
        parent = row.get("parent_id")
        if parent is not None and (not isinstance(parent, str) or not NODE_ID.fullmatch(parent) or not parent.startswith(provider + ":") or parent == node_id):
            raise ValueError("Invalid parent")
        status, evidence = row.get("status"), row.get("evidence")
        if status not in STATES or evidence not in EVIDENCE:
            raise ValueError("Invalid status")
        updated = row.get("updated_at")
        if updated is not None:
            updated = timestamp(updated).isoformat()
        nodes.append({
            "id": node_id, "provider": provider, "parent_id": parent,
            "title": safe_label(row.get("title", "")) or provider,
            "role": safe_label(row.get("role", ""), 80),
            "status": status, "evidence": evidence, "updated_at": updated,
        })
        seen.add(node_id)
    parents = {node["id"]: node["parent_id"] for node in nodes}
    for node in nodes:
        trail, current = set(), node["id"]
        while current is not None:
            if current in trail:
                raise ValueError("Cyclic hierarchy")
            trail.add(current)
            current = parents.get(current)
        # Keep an absent parent reference so the UI can mark an incomplete tree.
    return {
        "schema": 1, "collected_at": collected.isoformat(),
        "source_status": "stale" if (now - collected).total_seconds() > STALE_SECONDS else "connected",
        "stale_after_seconds": STALE_SECONDS,
        "nodes": nodes, "truncated": bool(raw.get("truncated", False)),
        "source_errors": [source for source in ("codex", "claude") if source in errors],
    }


def load_snapshot():
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    client = boto3.client(
        "s3", endpoint_url=settings.R2_ENDPOINT,
        aws_access_key_id=settings.R2_ACCESS_KEY,
        aws_secret_access_key=settings.R2_SECRET_KEY,
        region_name=getattr(settings, "R2_REGION", "auto"),
        config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1}),
    )
    try:
        obj = client.get_object(Bucket=settings.R2_STORAGE_BUCKET, Key=SNAPSHOT_KEY)
    except ClientError as exc:
        if str(exc.response.get("Error", {}).get("Code")) in {"NoSuchKey", "404"}:
            return {"schema": 1, "collected_at": None, "source_status": "unavailable", "stale_after_seconds": STALE_SECONDS, "nodes": [], "truncated": False, "source_errors": []}
        raise
    body = obj["Body"]
    try:
        encoded = body.read(MAX_BYTES + 1)
    finally:
        body.close()
    if len(encoded) > MAX_BYTES:
        raise ValueError("Snapshot exceeds limit")
    return normalize_snapshot(json.loads(encoded))
