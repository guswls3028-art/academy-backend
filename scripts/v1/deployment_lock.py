#!/usr/bin/env python3
"""Atomic cross-entrypoint deployment/cleanup lock backed by DynamoDB."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

LOCK_KEY = "__deployment_control__"
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
            "--condition-expression", "#owner = :owner",
            "--expression-attribute-names", json.dumps({"#owner": "owner"}),
            "--expression-attribute-values", json.dumps({":owner": {"S": owner}}),
        )
    except RuntimeError as exc:
        if "ConditionalCheckFailedException" in str(exc):
            raise RuntimeError("refusing to release a deployment lock owned by another process") from exc
        raise


def renew(table: str, owner: str, ttl_seconds: int) -> None:
    now = int(time.time())
    try:
        _aws(
            "update-item", "--table-name", table,
            "--key", json.dumps({"videoId": {"S": LOCK_KEY}}),
            "--update-expression", "SET #ttl = :expires",
            "--condition-expression", "#owner = :owner AND #ttl >= :now",
            "--expression-attribute-names", json.dumps({"#owner": "owner", "#ttl": "ttl"}),
            "--expression-attribute-values", json.dumps({
                ":owner": {"S": owner}, ":now": {"N": str(now)},
                ":expires": {"N": str(now + ttl_seconds)},
            }),
        )
    except RuntimeError as exc:
        if "ConditionalCheckFailedException" in str(exc):
            raise RuntimeError("cannot renew an expired lock or a lock owned by another process") from exc
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("acquire", "assert-owned", "renew", "release"))
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
        else:
            release(args.table_name, args.owner)
    except RuntimeError as exc:
        print(f"[deployment-lock] {exc}", file=sys.stderr)
        return 2
    print(f"[deployment-lock] {args.action} owner={args.owner} table={args.table_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
