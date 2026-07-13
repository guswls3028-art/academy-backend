#!/usr/bin/env python3
"""
AWS Batch Job Definition revision cleanup.

Each git push that touches batch.tf or job def parameters creates a new revision.
AWS keeps every revision ACTIVE forever (no built-in retention). Old revisions
clutter the Batch console and slow describe-job-definitions API calls.

Strategy: keep latest N ACTIVE revisions per job-def name, deregister the rest.
Deregistered revisions remain queryable for running jobs that referenced them.

Usage:
  python batch-jobdef-cleanup.py --dry-run            # show what would be removed
  python batch-jobdef-cleanup.py --execute            # deregister
  python batch-jobdef-cleanup.py --execute --keep 5   # keep latest 5 (default)

Hard-fails on any AWS error (matches ecr-cleanup.py pattern).
"""
import argparse
import atexit
import os
from pathlib import Path
import subprocess
import sys
import uuid
from collections import defaultdict

import boto3
from botocore.exceptions import ClientError

REGION = "ap-northeast-2"
LOCK_HELPER = Path(__file__).with_name("deployment_lock.py")


def require_cleanup_lock():
    owner = os.environ.get("ACADEMY_DEPLOY_LOCK_OWNER")
    owned_here = not owner
    owner = owner or f"batch-cleanup:{os.getpid()}:{uuid.uuid4().hex}"
    action = "acquire" if owned_here else "renew"
    result = subprocess.run(
        [sys.executable, str(LOCK_HELPER), action, "--owner", owner, "--ttl-seconds", "10800"],
        check=False,
    )
    if result.returncode:
        sys.exit(result.returncode)
    if owned_here:
        atexit.register(
            subprocess.run,
            [sys.executable, str(LOCK_HELPER), "release", "--owner", owner],
            check=False,
        )


def list_active_defs(batch):
    """Return {name: [(revision, arn), ...]} for all ACTIVE job definitions."""
    out = defaultdict(list)
    paginator = batch.get_paginator("describe_job_definitions")
    for page in paginator.paginate(status="ACTIVE"):
        for d in page["jobDefinitions"]:
            out[d["jobDefinitionName"]].append((d["revision"], d["jobDefinitionArn"]))
    return out


def main():
    ap = argparse.ArgumentParser(description="Batch job def revision cleanup")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--dry-run", action="store_true")
    grp.add_argument("--execute", action="store_true")
    ap.add_argument("--keep", type=int, default=5,
                    help="Number of latest ACTIVE revisions to keep per name (default: 5)")
    args = ap.parse_args()

    if args.execute:
        require_cleanup_lock()

    batch = boto3.client("batch", region_name=REGION)
    defs = list_active_defs(batch)

    if not defs:
        print("No ACTIVE job definitions found.")
        return

    total_drop = 0
    total_keep = 0
    plan = []
    for name, revs in sorted(defs.items()):
        revs.sort(reverse=True)
        keep = revs[: args.keep]
        drop = revs[args.keep :]
        total_keep += len(keep)
        total_drop += len(drop)
        kept_str = ",".join(str(r) for r, _ in keep)
        dropped_str = ",".join(str(r) for r, _ in drop) if drop else "none"
        print(f"  {name}: keep={kept_str}  drop={dropped_str}")
        plan.append((name, drop))

    print(f"\nTotals: keep={total_keep}, drop={total_drop}")

    if args.dry_run:
        print("[DRY-RUN] No deregistrations performed.")
        return

    if total_drop == 0:
        print("Nothing to deregister.")
        return

    deregistered = 0
    failed = 0
    for name, drop in plan:
        for rev, _arn in drop:
            try:
                batch.deregister_job_definition(jobDefinition=f"{name}:{rev}")
                deregistered += 1
            except ClientError as e:
                failed += 1
                print(f"  [ERROR] {name}:{rev} - {e}", file=sys.stderr)

    print(f"\nDeregistered: {deregistered}, failed: {failed}")
    if failed:
        sys.exit(2)


if __name__ == "__main__":
    main()
