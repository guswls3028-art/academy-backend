#!/usr/bin/env python3
"""
GitHub Actions Artifact Cleanup

Purpose: Delete old Actions artifacts that have accumulated past any reasonable
retention window. `docker/build-push-action@v6` without `provenance: false`
silently produces `*.dockerbuild` artifacts on every build — the backend repo
accumulated 22,000+ such records.

Strategy: Keep artifacts created within KEEP_DAYS (default 7). Delete everything
older. Uses concurrent DELETE /repos/{owner}/{repo}/actions/artifacts/{id}.

Usage:
  python gh-artifact-cleanup.py --repo guswls3028-art/academy-backend --dry-run
  python gh-artifact-cleanup.py --repo guswls3028-art/academy-backend --execute
  python gh-artifact-cleanup.py --repo guswls3028-art/academy-backend --execute --keep-days 7
"""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta


def gh_api(path: str, method: str = "GET") -> dict:
    cmd = ["gh", "api", "-X", method, path]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return {"_error": r.stderr.strip(), "_returncode": r.returncode}
    try:
        return json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        return {"_raw": r.stdout}


def list_all_artifacts(repo: str) -> list[dict]:
    """Legacy entrypoint kept for compatibility — _safe_list returns (list, ok)."""
    arts, _ok = _safe_list(repo)
    return arts


def _safe_list(repo: str) -> tuple[list[dict], bool]:
    artifacts: list[dict] = []
    page = 1
    while True:
        data = gh_api(f"/repos/{repo}/actions/artifacts?per_page=100&page={page}")
        if data.get("_error"):
            print(f"[ERROR] list page {page}: {data['_error']}", file=sys.stderr)
            return artifacts, False
        batch = data.get("artifacts", [])
        if not batch:
            break
        artifacts.extend(batch)
        total = data.get("total_count", 0)
        print(f"  fetched page {page}: {len(batch)} artifacts (cumulative {len(artifacts)}/{total})")
        if len(artifacts) >= total:
            break
        page += 1
    return artifacts, True


def delete_artifact(repo: str, artifact_id: int) -> tuple[int, bool, str]:
    r = subprocess.run(
        ["gh", "api", "-X", "DELETE", f"/repos/{repo}/actions/artifacts/{artifact_id}"],
        capture_output=True, text=True, check=False,
    )
    ok = r.returncode == 0
    err = r.stderr.strip() if not ok else ""
    return artifact_id, ok, err


def check_rate_limit() -> tuple[int, int]:
    """Returns (remaining, reset_epoch)."""
    r = subprocess.run(["gh", "api", "rate_limit"], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return 5000, 0
    try:
        d = json.loads(r.stdout)
        c = d["resources"]["core"]
        return c["remaining"], c["reset"]
    except (json.JSONDecodeError, KeyError):
        return 5000, 0


def wait_for_rate_limit(reset_epoch: int):
    import time
    wait = max(reset_epoch - int(time.time()), 0) + 5
    if wait > 0:
        print(f"  [rate-limit] sleeping {wait}s until reset...")
        time.sleep(wait)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="owner/repo")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--execute", action="store_true")
    parser.add_argument("--keep-days", type=int, default=7)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.keep_days)
    print(f"Repo: {args.repo}")
    print(f"Cutoff: artifacts created before {cutoff.isoformat()} will be deleted")
    print()

    print("Listing artifacts...")
    artifacts, list_ok = _safe_list(args.repo)
    print(f"Total artifacts discovered: {len(artifacts)}")
    if not list_ok:
        # Truncated list would silently shrink delete set — refuse to proceed.
        print("[ERROR] artifact listing failed mid-pagination; aborting to avoid partial run.", file=sys.stderr)
        sys.exit(2)

    expired = [a for a in artifacts if a.get("expired")]
    active = [a for a in artifacts if not a.get("expired")]

    def is_old(a):
        created = a.get("created_at", "")
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            return dt < cutoff
        except (ValueError, TypeError):
            return False

    to_delete = [a for a in artifacts if is_old(a)]
    to_keep = [a for a in artifacts if not is_old(a)]

    total_bytes_delete = sum(a.get("size_in_bytes", 0) for a in to_delete)
    total_bytes_keep = sum(a.get("size_in_bytes", 0) for a in to_keep)

    print()
    print(f"Active:  {len(active)}")
    print(f"Expired: {len(expired)}")
    print(f"Keep (within {args.keep_days} days):  {len(to_keep)} ({total_bytes_keep/1024/1024:.1f} MB)")
    print(f"Delete (older):                      {len(to_delete)} ({total_bytes_delete/1024/1024:.1f} MB)")

    if not to_delete:
        print("Nothing to delete.")
        return

    if args.dry_run:
        print("\n[DRY-RUN] No deletions performed.")
        return

    print(f"\nDeleting {len(to_delete)} artifacts with {args.workers} workers (auto-waits on rate limit)...")
    deleted = 0
    failed = 0
    pending = [a["id"] for a in to_delete]
    CHUNK = 4000  # stay under 5000/hr limit
    pos = 0
    while pos < len(pending):
        remaining, reset_at = check_rate_limit()
        if remaining < 100:
            wait_for_rate_limit(reset_at)
            remaining, _ = check_rate_limit()
        # Process up to min(CHUNK, remaining - buffer)
        batch_size = min(CHUNK, max(remaining - 50, 0), len(pending) - pos)
        if batch_size <= 0:
            wait_for_rate_limit(reset_at)
            continue
        batch = pending[pos:pos + batch_size]
        pos += batch_size
        print(f"\nChunk: processing {len(batch)} (rate_remaining={remaining})")
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(delete_artifact, args.repo, aid): aid for aid in batch}
            for i, fut in enumerate(as_completed(futures), 1):
                aid, ok, err = fut.result()
                if ok:
                    deleted += 1
                else:
                    failed += 1
                    if failed <= 5:
                        print(f"  [FAIL] id={aid}: {err}", file=sys.stderr)
                if (deleted + failed) % 500 == 0:
                    print(f"  progress: total {deleted+failed}/{len(to_delete)} (deleted={deleted}, failed={failed})")

    print(f"\nResult: deleted={deleted}, failed={failed}")
    print(f"Est. reclaimed storage: {total_bytes_delete/1024/1024:.1f} MB")
    if failed:
        # Hard-fail so cron doesn't report success while leaking errors.
        sys.exit(2)


if __name__ == "__main__":
    main()
