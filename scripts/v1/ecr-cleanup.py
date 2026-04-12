#!/usr/bin/env python3
"""
ECR Manifest-Aware Cleanup Script

문제: OCI Image Index (multi-arch manifest list) 구조에서
- ECR lifecycle policy가 실제 삭제에 실패 (child manifest 참조 문제)
- sha- 태그가 무한 누적 (lifecycle keep 5인데 33개 잔존)

해법:
1. latest + 최신 sha- N개만 보존
2. 보존 대상 외 tagged index를 먼저 삭제 (자식 참조 해제)
3. 고아된 untagged manifest 삭제

사용법:
  python ecr-cleanup.py --dry-run                    # 삭제 대상만 출력
  python ecr-cleanup.py --dry-run --repo academy-api  # 단일 리포
  python ecr-cleanup.py --execute                     # 실제 삭제
  python ecr-cleanup.py --execute --keep 5            # 최신 5개만 보존
  python ecr-cleanup.py --verify                      # 정리 후 검증
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone

REGION = "ap-northeast-2"

REPOS = [
    "academy-base",
    "academy-api",
    "academy-ai-worker-cpu",
    "academy-messaging-worker",
    "academy-video-worker",
]

# OCI manifest types
TYPE_INDEX = "application/vnd.oci.image.index.v1+json"
TYPE_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
TYPE_DOCKER_LIST = "application/vnd.docker.distribution.manifest.list.v2+json"
TYPE_DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"

INDEX_TYPES = {TYPE_INDEX, TYPE_DOCKER_LIST}
MANIFEST_TYPES = {TYPE_MANIFEST, TYPE_DOCKER_MANIFEST}

# Tags that must never be deleted
ALWAYS_KEEP_TAGS = {"latest"}


def aws_ecr(*args) -> str:
    """Run aws ecr command and return stdout."""
    cmd = ["aws", "ecr", "--region", REGION] + list(args) + ["--output", "json"]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        print(f"  [ERROR] {' '.join(cmd[:6])}: {r.stderr.strip()}", file=sys.stderr)
        return "{}"
    return r.stdout


def get_all_images(repo: str) -> list[dict]:
    """Get all images with pagination."""
    images = []
    token = None
    while True:
        extra = ["--next-token", token] if token else []
        raw = aws_ecr("describe-images", "--repository-name", repo, *extra)
        data = json.loads(raw)
        images.extend(data.get("imageDetails", []))
        token = data.get("nextToken")
        if not token:
            break
    return images


def parse_pushed_at(img: dict) -> datetime:
    """Parse imagePushedAt to datetime for sorting."""
    pushed = img.get("imagePushedAt", "")
    if isinstance(pushed, (int, float)):
        return datetime.fromtimestamp(pushed, tz=timezone.utc)
    try:
        # AWS CLI returns ISO format strings
        return datetime.fromisoformat(str(pushed).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def resolve_child_digests(repo: str, index_digest: str) -> list[str]:
    """Fetch child manifest digests from an index manifest."""
    raw = aws_ecr(
        "batch-get-image",
        "--repository-name", repo,
        "--image-ids", json.dumps([{"imageDigest": index_digest}]),
        "--accepted-media-types", json.dumps(list(INDEX_TYPES)),
    )
    data = json.loads(raw)
    children = []
    for image in data.get("images", []):
        manifest_str = image.get("imageManifest", "{}")
        try:
            manifest = json.loads(manifest_str)
        except json.JSONDecodeError:
            continue
        for m in manifest.get("manifests", []):
            child_digest = m.get("digest", "")
            if child_digest:
                children.append(child_digest)
    return children


def identify_protected_set(
    images: list[dict], keep: int, repo: str
) -> tuple[set[str], list[dict]]:
    """
    Determine which digests to protect.

    Protected:
    - 'latest' tag and its children (always)
    - Newest `keep` sha- tagged images and their children

    Returns: (protected_digests, deletable_tagged_images)
    """
    protected = set()
    tagged_images = []

    for img in images:
        tags = img.get("imageTags", [])
        if tags:
            tagged_images.append(img)

    # Separate always-keep from sha- tagged
    always_keep = []
    sha_tagged = []

    for img in tagged_images:
        tags = set(img.get("imageTags", []))
        if tags & ALWAYS_KEEP_TAGS:
            always_keep.append(img)
        elif any(t.startswith("sha-") for t in tags):
            sha_tagged.append(img)
        else:
            # Unknown tag pattern — protect by default
            always_keep.append(img)

    # Sort sha- by push time, newest first
    sha_tagged.sort(key=parse_pushed_at, reverse=True)

    # Protect: always_keep + newest N sha-
    kept_sha = sha_tagged[:keep]
    deletable_sha = sha_tagged[keep:]

    for img in always_keep + kept_sha:
        digest = img["imageDigest"]
        protected.add(digest)

        # Resolve and protect children of protected indexes
        media_type = img.get("imageManifestMediaType", "")
        if media_type in INDEX_TYPES:
            children = resolve_child_digests(repo, digest)
            protected.update(children)

    # Log
    sha_tags_kept = []
    for img in kept_sha:
        tags = [t for t in img.get("imageTags", []) if t.startswith("sha-")]
        sha_tags_kept.extend(tags)

    sha_tags_deleted = []
    for img in deletable_sha:
        tags = [t for t in img.get("imageTags", []) if t.startswith("sha-")]
        sha_tags_deleted.extend(tags)

    print(f"  Protected tags: latest + {len(kept_sha)} newest sha- = {len(protected)} digests")
    if kept_sha:
        oldest_kept = kept_sha[-1]
        oldest_tag = [t for t in oldest_kept.get("imageTags", []) if t.startswith("sha-")]
        print(f"  Oldest kept sha-: {oldest_tag[0] if oldest_tag else '?'} "
              f"({parse_pushed_at(oldest_kept).strftime('%Y-%m-%d %H:%M')})")
    if deletable_sha:
        print(f"  Deletable sha- tags: {len(deletable_sha)}")

    return protected, deletable_sha


def classify_deletable(
    images: list[dict],
    protected: set[str],
    deletable_tagged: list[dict],
) -> tuple[list[str], list[str]]:
    """
    Classify deletable images into ordered phases:
    - Phase A: tagged indexes to untag+delete (old sha- parents)
    - Phase B: untagged indexes (orphaned parents)
    - Phase C: untagged manifests (orphaned children)

    Returns: (indexes_to_delete, manifests_to_delete)
    """
    indexes = []
    manifests = []

    # Old sha- tagged indexes → delete first
    for img in deletable_tagged:
        digest = img["imageDigest"]
        media_type = img.get("imageManifestMediaType", "")
        if media_type in INDEX_TYPES:
            indexes.append(digest)
        else:
            manifests.append(digest)

    # Untagged images
    for img in images:
        digest = img["imageDigest"]
        tags = img.get("imageTags", [])
        media_type = img.get("imageManifestMediaType", "")

        if tags or digest in protected:
            continue

        if media_type in INDEX_TYPES:
            indexes.append(digest)
        else:
            manifests.append(digest)

    return indexes, manifests


def batch_delete(repo: str, digests: list[str], label: str) -> tuple[int, int]:
    """Delete images in batches of 100. Returns (deleted, failed)."""
    if not digests:
        return 0, 0

    total_deleted = 0
    total_failed = 0

    for i in range(0, len(digests), 100):
        batch = digests[i : i + 100]
        image_ids = [{"imageDigest": d} for d in batch]
        ids_json = json.dumps(image_ids)

        raw = aws_ecr(
            "batch-delete-image",
            "--repository-name", repo,
            "--image-ids", ids_json,
        )
        result = json.loads(raw) if raw.strip() and raw.strip() != "{}" else {}

        deleted = len(result.get("imageIds", []))
        failed = len(result.get("failures", []))
        total_deleted += deleted
        total_failed += failed

        if failed > 0:
            reasons = defaultdict(int)
            for f in result.get("failures", []):
                reasons[f.get("failureCode", "Unknown")] += 1
            reason_str = ", ".join(f"{k}:{v}" for k, v in reasons.items())
            print(f"    {label} batch {i // 100 + 1}: deleted={deleted}, failed={failed} ({reason_str})")
        else:
            print(f"    {label} batch {i // 100 + 1}: deleted={deleted}")

    return total_deleted, total_failed


def cleanup_repo(repo: str, keep: int, execute: bool) -> dict:
    """Clean up a single repository."""
    print(f"\n{'=' * 60}")
    print(f"  Repository: {repo}")
    print(f"{'=' * 60}")

    images = get_all_images(repo)
    total = len(images)
    tagged_count = sum(1 for img in images if img.get("imageTags"))
    untagged_count = total - tagged_count
    print(f"  Total images: {total} (tagged={tagged_count}, untagged={untagged_count})")

    # Identify protected set (resolves child digests for protected indexes)
    protected, deletable_tagged = identify_protected_set(images, keep, repo)

    # Classify deletable
    del_indexes, del_manifests = classify_deletable(images, protected, deletable_tagged)
    total_deletable = len(del_indexes) + len(del_manifests)
    print(f"  Deletable: {total_deletable} (indexes={len(del_indexes)}, manifests={len(del_manifests)})")

    # Calculate storage savings
    deletable_digests = set(del_indexes + del_manifests)
    deletable_bytes = sum(
        img.get("imageSizeInBytes", 0)
        for img in images
        if img["imageDigest"] in deletable_digests
    )
    deletable_gb = deletable_bytes / (1024**3)
    print(f"  Reclaimable storage: {deletable_gb:.2f} GB")

    result = {
        "repo": repo,
        "total_before": total,
        "tagged_before": tagged_count,
        "untagged_before": untagged_count,
        "deletable_indexes": len(del_indexes),
        "deletable_manifests": len(del_manifests),
        "deletable_gb": deletable_gb,
        "deleted": 0,
        "failed": 0,
    }

    if not execute:
        print("  [DRY-RUN] No deletions performed.")
        return result

    # Phase A: Delete old tagged + untagged indexes (releases child references)
    if del_indexes:
        print(f"\n  Phase A: Deleting {len(del_indexes)} index manifests (parents)...")
        d, f = batch_delete(repo, del_indexes, "index")
        result["deleted"] += d
        result["failed"] += f

    # Phase B: Delete orphaned platform manifests (children freed by Phase A)
    if del_manifests:
        print(f"\n  Phase B: Deleting {len(del_manifests)} platform manifests (children)...")
        d, f = batch_delete(repo, del_manifests, "manifest")
        result["deleted"] += d
        result["failed"] += f

    # Phase C: Re-scan for any newly orphaned untagged images
    # (children of indexes deleted in Phase A that weren't in our initial list)
    print("\n  Phase C: Re-scanning for newly orphaned images...")
    images_after = get_all_images(repo)
    remaining_untagged = [
        img["imageDigest"]
        for img in images_after
        if not img.get("imageTags") and img["imageDigest"] not in protected
    ]
    if remaining_untagged:
        print(f"  Phase C: Found {len(remaining_untagged)} newly orphaned images, deleting...")
        d, f = batch_delete(repo, remaining_untagged, "orphan")
        result["deleted"] += d
        result["failed"] += f
    else:
        print("  Phase C: No newly orphaned images.")

    print(f"\n  Result: deleted={result['deleted']}, failed={result['failed']}")
    return result


def verify_repo(repo: str) -> dict:
    """Post-cleanup verification."""
    print(f"\n  {repo}:")
    images = get_all_images(repo)
    total = len(images)
    tagged = sum(1 for img in images if img.get("imageTags"))
    untagged = total - tagged

    total_bytes = sum(img.get("imageSizeInBytes", 0) for img in images)
    total_gb = total_bytes / (1024**3)

    # Lifecycle policy check
    raw = aws_ecr("get-lifecycle-policy", "--repository-name", repo)
    data = json.loads(raw) if raw.strip() else {}
    last_eval = "UNKNOWN"
    if "lastEvaluatedAt" in data:
        last_eval = str(data["lastEvaluatedAt"])

    status = "OK" if untagged <= tagged * 2 else "WARN"
    icon = "[OK]" if status == "OK" else "[WARN]"

    print(f"    Images: {total} (tagged={tagged}, untagged={untagged})")
    print(f"    Storage: {total_gb:.2f} GB")
    print(f"    Lifecycle lastEvaluatedAt: {last_eval}")
    print(f"    {icon} {'Ratio acceptable' if status == 'OK' else f'Untagged ({untagged}) high vs tagged ({tagged})'}")

    return {"repo": repo, "total": total, "tagged": tagged, "untagged": untagged, "gb": total_gb, "status": status}


def main():
    parser = argparse.ArgumentParser(
        description="ECR Manifest-Aware Cleanup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --dry-run                    # Show what would be deleted (all repos)
  %(prog)s --dry-run --repo academy-api # Single repo dry-run
  %(prog)s --execute --keep 10          # Delete, keep latest + 10 sha- tags
  %(prog)s --verify                     # Post-cleanup health check
        """,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Show what would be deleted")
    group.add_argument("--execute", action="store_true", help="Actually delete images")
    group.add_argument("--verify", action="store_true", help="Post-cleanup verification")
    parser.add_argument("--repo", choices=REPOS, help="Single repo (default: all)")
    parser.add_argument("--keep", type=int, default=10,
                        help="Number of sha- tagged images to keep per repo (default: 10)")
    args = parser.parse_args()

    repos = [args.repo] if args.repo else REPOS

    if args.verify:
        print(f"\n{'=' * 60}")
        print("  ECR POST-CLEANUP VERIFICATION")
        print(f"{'=' * 60}")
        results = [verify_repo(r) for r in repos]
        total_gb = sum(r["gb"] for r in results)
        total_images = sum(r["total"] for r in results)
        print(f"\n  Total: {total_images} images, {total_gb:.2f} GB")
        warns = [r for r in results if r["status"] == "WARN"]
        if warns:
            print(f"  {len(warns)} repo(s) need attention.")
        else:
            print("  All repos healthy.")
        return

    mode = "DRY-RUN" if args.dry_run else "EXECUTE"
    print(f"\n{'=' * 60}")
    print(f"  ECR MANIFEST-AWARE CLEANUP ({mode})")
    print(f"  Keep: latest + newest {args.keep} sha- tags per repo")
    print(f"{'=' * 60}")

    if args.execute:
        print("\n  [!] EXECUTE mode: images will be permanently deleted.")
        print("     Press Ctrl+C within 5 seconds to abort...")
        import time
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n  Aborted.")
            sys.exit(1)

    results = []
    for repo in repos:
        r = cleanup_repo(repo, keep=args.keep, execute=args.execute)
        results.append(r)

    # Summary
    print(f"\n{'=' * 60}")
    print("  SUMMARY")
    print(f"{'=' * 60}")

    for r in results:
        d = r["deletable_indexes"] + r["deletable_manifests"]
        print(f"  {r['repo']}: {d} deletable ({r['deletable_gb']:.1f} GB)"
              + (f" -> deleted={r['deleted']}, failed={r['failed']}" if args.execute else ""))

    total_deletable = sum(r["deletable_indexes"] + r["deletable_manifests"] for r in results)
    total_gb = sum(r["deletable_gb"] for r in results)
    total_deleted = sum(r["deleted"] for r in results)
    total_failed = sum(r["failed"] for r in results)

    print(f"\n  Total: {total_deletable} images, {total_gb:.1f} GB reclaimable")
    if args.execute:
        print(f"  Deleted: {total_deleted}, Failed: {total_failed}")
        print(f"  Est. monthly savings: ${total_gb * 0.10:.2f}/mo")
        if total_failed > 0:
            print("  [WARN] Some deletions failed. Run --verify to check, then --execute again.")
        else:
            print("  Run --verify to confirm cleanup.")
    else:
        print(f"  Est. monthly savings: ${total_gb * 0.10:.2f}/mo")
        print(f"\n  Run with --execute to perform deletion.")


if __name__ == "__main__":
    main()
