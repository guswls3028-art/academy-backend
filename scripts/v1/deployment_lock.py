#!/usr/bin/env python3
"""Atomic cross-entrypoint deployment/cleanup lock backed by DynamoDB."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

LOCK_KEY = "__deployment_control_v2__"
LEGACY_LOCK_KEY = "__deployment_control__"
LEGACY_SEAL_TTL = 4_102_444_800  # 2100-01-01 UTC
WINDOW_PIN_TTL = LEGACY_SEAL_TTL
DEFAULT_TABLE = "academy-v1-video-job-lock"
DEFAULT_REGION = "ap-northeast-2"


def _aws(*args: str) -> dict:
    command = ["aws", "dynamodb", *args, "--output", "json"]
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION
    command += ["--region", region]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout or "{}")


def acquire(table: str, owner: str, ttl_seconds: int) -> None:
    now = int(time.time())
    item = {
        "videoId": {"S": LOCK_KEY}, "owner": {"S": owner},
        "ttl": {"N": str(now + ttl_seconds)}, "acquiredAt": {"N": str(now)},
    }
    try:
        _aws(
            "put-item", "--table-name", table,
            "--item", json.dumps(item, separators=(",", ":")),
            "--condition-expression", "attribute_not_exists(videoId) OR #ttl < :now",
            "--expression-attribute-names", json.dumps({"#ttl": "ttl"}),
            "--expression-attribute-values", json.dumps({":now": {"N": str(now)}}),
        )
    except RuntimeError as exc:
        if "ConditionalCheckFailedException" in str(exc):
            raise RuntimeError(f"deployment lock is already held in {table}") from exc
        raise


def assert_owned(table: str, owner: str) -> None:
    result = _aws(
        "get-item", "--table-name", table,
        "--key", json.dumps({"videoId": {"S": LOCK_KEY}}), "--consistent-read",
    )
    item = result.get("Item", {})
    actual = item.get("owner", {}).get("S")
    expires = int(item.get("ttl", {}).get("N", "0"))
    if actual != owner or expires <= int(time.time()):
        raise RuntimeError(f"deployment lock ownership mismatch: expected={owner!r} actual={actual!r}")


def release(table: str, owner: str) -> None:
    try:
        _aws(
            "delete-item", "--table-name", table,
            "--key", json.dumps({"videoId": {"S": LOCK_KEY}}),
            "--condition-expression", (
                "#owner = :owner AND attribute_not_exists(#window) AND attribute_not_exists(#windowTtl)"
            ),
            "--expression-attribute-names", json.dumps({
                "#owner": "owner", "#window": "accountWindow", "#windowTtl": "accountWindowTtl",
            }),
            "--expression-attribute-values", json.dumps({":owner": {"S": owner}}),
        )
    except RuntimeError as exc:
        if "ConditionalCheckFailedException" in str(exc):
            raise RuntimeError("refusing to release another owner's lock or an unrestored account window") from exc
        raise


def renew(table: str, owner: str, ttl_seconds: int) -> None:
    now = int(time.time())
    try:
        _aws(
            "update-item", "--table-name", table,
            "--key", json.dumps({"videoId": {"S": LOCK_KEY}}),
            "--update-expression", "SET #ttl = if_not_exists(#windowTtl, :expires)",
            "--condition-expression", (
                "#owner = :owner AND #ttl >= :now AND "
                "((attribute_not_exists(#window) AND attribute_not_exists(#windowTtl)) OR "
                "(attribute_exists(#window) AND #windowTtl = :pinned))"
            ),
            "--expression-attribute-names", json.dumps({
                "#owner": "owner", "#ttl": "ttl", "#window": "accountWindow",
                "#windowTtl": "accountWindowTtl",
            }),
            "--expression-attribute-values", json.dumps({
                ":owner": {"S": owner}, ":now": {"N": str(now)},
                ":expires": {"N": str(now + ttl_seconds)},
                ":pinned": {"N": str(WINDOW_PIN_TTL)},
            }),
        )
    except RuntimeError as exc:
        if "ConditionalCheckFailedException" in str(exc):
            raise RuntimeError("cannot renew an expired lock or a lock owned by another process") from exc
        raise


def _window_json(state: dict) -> str:
    if not isinstance(state, dict) or not state:
        raise ValueError("account window state must be a nonempty object")
    return json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False)


def load_window(table: str, owner: str) -> dict | None:
    result = _aws(
        "get-item", "--table-name", table,
        "--key", json.dumps({"videoId": {"S": LOCK_KEY}}), "--consistent-read",
    )
    item = result.get("Item", {})
    if item.get("owner", {}).get("S") != owner or int(item.get("ttl", {}).get("N", "0")) <= int(time.time()):
        raise RuntimeError("account window requires the current live deployment owner")
    raw = item.get("accountWindow", {}).get("S")
    if raw is None:
        if "accountWindowTtl" in item:
            raise RuntimeError("orphaned account window pin requires recovery")
        return None
    if (item.get("accountWindowTtl") != {"N": str(WINDOW_PIN_TTL)}
            or item.get("ttl") != {"N": str(WINDOW_PIN_TTL)}):
        raise RuntimeError("account window pin is inconsistent")
    state = json.loads(raw)
    _window_json(state)
    return state


def save_window(table: str, owner: str, state: dict, expected: dict | None = None) -> None:
    """Persist intent before mutation; pin this same lock until verified restoration."""
    values = {
        ":owner": {"S": owner}, ":now": {"N": str(int(time.time()))},
        ":pinned": {"N": str(WINDOW_PIN_TTL)}, ":state": {"S": _window_json(state)},
    }
    condition = "attribute_not_exists(#window) AND attribute_not_exists(#windowTtl)"
    if expected is not None:
        condition = "#window = :previous AND #windowTtl = :pinned"
        values[":previous"] = {"S": _window_json(expected)}
    _aws(
        "update-item", "--table-name", table,
        "--key", json.dumps({"videoId": {"S": LOCK_KEY}}),
        "--update-expression", "SET #window = :state, #windowTtl = :pinned, #ttl = :pinned",
        "--condition-expression", f"#owner = :owner AND #ttl >= :now AND {condition}",
        "--expression-attribute-names", json.dumps({
            "#owner": "owner", "#ttl": "ttl", "#window": "accountWindow",
            "#windowTtl": "accountWindowTtl",
        }),
        "--expression-attribute-values", json.dumps(values),
    )


def clear_window(table: str, owner: str, expected: dict) -> None:
    """Called only after the cutover owner verifies actual restoration of its plan."""
    now = int(time.time())
    _aws(
        "update-item", "--table-name", table,
        "--key", json.dumps({"videoId": {"S": LOCK_KEY}}),
        "--update-expression", "SET #ttl = :expires REMOVE #window, #windowTtl",
        "--condition-expression", (
            "#owner = :owner AND #ttl = :pinned AND "
            "#windowTtl = :pinned AND #window = :previous"
        ),
        "--expression-attribute-names", json.dumps({
            "#owner": "owner", "#ttl": "ttl", "#window": "accountWindow",
            "#windowTtl": "accountWindowTtl",
        }),
        "--expression-attribute-values", json.dumps({
            ":owner": {"S": owner}, ":pinned": {"N": str(WINDOW_PIN_TTL)},
            ":previous": {"S": _window_json(expected)}, ":expires": {"N": str(now + 10_800)},
        }),
    )


def seal_legacy(table: str, owner: str) -> None:
    """Permanently fence workflows that still use the retired v1 lock key."""
    now = int(time.time())
    item = {
        "videoId": {"S": LEGACY_LOCK_KEY},
        "owner": {"S": f"retired:{owner}"},
        "ttl": {"N": str(LEGACY_SEAL_TTL)},
        "acquiredAt": {"N": str(now)},
        "retiredAt": {"N": str(now)},
    }
    try:
        _aws(
            "put-item",
            "--table-name",
            table,
            "--item",
            json.dumps(item, separators=(",", ":")),
            "--condition-expression",
            "attribute_not_exists(videoId) OR #ttl < :now OR begins_with(#owner, :retired)",
            "--expression-attribute-names",
            json.dumps({"#ttl": "ttl", "#owner": "owner"}),
            "--expression-attribute-values",
            json.dumps({
                ":now": {"N": str(now)},
                ":retired": {"S": "retired:"},
            }),
        )
    except RuntimeError as exc:
        if "ConditionalCheckFailedException" in str(exc):
            raise RuntimeError(
                "legacy deployment lock is active; wait for its owner before sealing"
            ) from exc
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("acquire", "assert-owned", "renew", "release", "seal-legacy"),
    )
    parser.add_argument("--owner", required=True)
    parser.add_argument("--table-name", default=os.environ.get("ACADEMY_DEPLOY_LOCK_TABLE", DEFAULT_TABLE))
    parser.add_argument("--ttl-seconds", type=int, default=10_800)
    args = parser.parse_args()
    if not args.owner.strip():
        parser.error("--owner must not be blank")
    if args.ttl_seconds < 300:
        parser.error("--ttl-seconds must be at least 300")
    return args


def main() -> int:
    args = parse_args()
    try:
        if args.action == "acquire":
            acquire(args.table_name, args.owner, args.ttl_seconds)
        elif args.action == "assert-owned":
            assert_owned(args.table_name, args.owner)
        elif args.action == "renew":
            renew(args.table_name, args.owner, args.ttl_seconds)
        elif args.action == "release":
            release(args.table_name, args.owner)
        else:
            seal_legacy(args.table_name, args.owner)
    except RuntimeError as exc:
        print(f"[deployment-lock] {exc}", file=sys.stderr)
        return 2
    print(f"[deployment-lock] {args.action} owner={args.owner} table={args.table_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
