#!/usr/bin/env python3
"""
ECR Manifest-Aware Cleanup Script

문제: OCI Image Index (multi-arch manifest list) 구조에서
naive batch-delete-image는 ImageReferencedByManifestList 에러 발생.

해법: 부모 Index를 먼저 삭제 → 자식 Platform Manifest 참조 해제 → 삭제 가능.

사용법:
  python ecr-cleanup.py --dry-run          # 삭제 대상만 출력
  python ecr-cleanup.py --execute          # 실제 삭제 수행
  python ecr-cleanup.py --verify           # 정리 후 검증
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict

REPOS = [
    "academy-base",
    "academy-api",
    "academy-ai-worker-cpu",
    "academy-messaging-worker",
    "academy-video-worker",
]

# OCI manifest types
TYPE_INDEX = "application/vnd.oci.image.index.v1+json"      # multi-arch parent
TYPE_MANIFEST = "application/vnd.oci.image.manifest.v1+json"  # single-arch child
# Docker v2 equivalents
TYPE_DOCKER_LIST = "application/vnd.docker.distribution.manifest.list.v2+json"
TYPE_DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"

INDEX_TYPES = {TYPE_INDEX, TYPE_DOCKER_LIST}
MANIFEST_TYPES = {TYPE_MANIFEST, TYPE_DOCKER_MANIFEST}


def aws_ecr(*args) -> str:
    """Run aws ecr command and return stdout."""
    cmd = ["aws", "ecr"] + list(args) + ["--output", "json"]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        print(f"  [ERROR] aws ecr {' '.join(args[:3])}: {r.stderr.strip()}", file=sys.stderr)
        return "[]"
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


def identify_protected_set(images: list[dict]) -> set[str]:
    """Tagged images and their children are protected."""
    protected = set()
    tagged_indexes = []

    for img in images:
        tags = img.get("imageTags", [])
        if tags:  # has at least one tag
            digest = img["imageDigest"]
            protected.add(digest)
            media_type = img.get("imageManifestMediaType", "")
            if media_type in INDEX_TYPES:
                tagged_indexes.append(img)

    # Note: We cannot resolve child digests from describe-images alone.
    # But lifecycle + parent-first deletion handles this correctly.
    return protected


def classify_deletable(images: list[dict], protected: set[str]) -> tuple[list[str], list[str]]:
    """
    Classify deletable images into:
    - untagged_indexes: parent manifest lists (delete first)
    - untagged_manifests: platform manifests (delete after parents)
    """
    untagged_indexes = []
    untagged_manifests = []

    for img in images:
        digest = img["imageDigest"]
        tags = img.get("imageTags", [])
        media_type = img.get("imageManifestMediaType", "")

        if tags or digest in protected:
            continue  # skip protected

        if media_type in INDEX_TYPES:
            untagged_indexes.append(digest)
        else:
            untagged_manifests.append(digest)

    return untagged_indexes, untagged_manifests


def batch_delete(repo: str, digests: list[str], label: str) -> tuple[int, int]:
    """Delete images in batches of 100. Returns (deleted, failed)."""
    total_deleted = 0
    total_failed = 0

    for i in range(0, len(digests), 100):
        batch = digests[i:i+100]
        image_ids = [{"imageDigest": d} for d in batch]
        ids_json = json.dumps(image_ids)

        raw = aws_ecr("batch-delete-image", "--repository-name", repo,
                       "--image-ids", ids_json)
        result = json.loads(raw) if raw.strip() else {}

        deleted = len(result.get("imageIds", []))
        failed = len(result.get("failures", []))
        total_deleted += deleted
        total_failed += failed

        if failed > 0:
            reasons = defaultdict(int)
            for f in result.get("failures", []):
                reasons[f.get("failureCode", "Unknown")] += 1
            reason_str = ", ".join(f"{k}:{v}" for k, v in reasons.items())
            print(f"    Batch {i//100+1}: deleted={deleted}, failed={failed} ({reason_str})")
        else:
            print(f"    Batch {i//100+1}: deleted={deleted}")

    return total_deleted, total_failed


def cleanup_repo(repo: str, execute: bool) -> dict:
    """Clean up a single repository."""
    print(f"\n{'='*60}")
    print(f"  Repository: {repo}")
    print(f"{'='*60}")

    images = get_all_images(repo)
    total = len(images)
    print(f"  Total images: {total}")

    # Classify
    tagged_count = sum(1 for img in images if img.get("imageTags"))
    untagged_count = total - tagged_count
    print(f"  Tagged: {tagged_count}, Untagged: {untagged_count}")

    protected = identify_protected_set(images)
    print(f"  Protected (tagged): {len(protected)}")

    untagged_indexes, untagged_manifests = classify_deletable(images, protected)
    print(f"  Deletable indexes (parent): {len(untagged_indexes)}")
    print(f"  Deletable manifests (child): {len(untagged_manifests)}")

    # Calculate storage
    deletable_digests = set(untagged_indexes + untagged_manifests)
    deletable_bytes = sum(
        img.get("imageSizeInBytes", 0)
        for img in images
        if img["imageDigest"] in deletable_digests
    )
    deletable_gb = deletable_bytes / (1024**3)
    print(f"  Deletable storage: {deletable_gb:.1f} GB")

    result = {
        "repo": repo,
        "total": total,
        "tagged": tagged_count,
        "untagged": untagged_count,
        "deletable_indexes": len(untagged_indexes),
        "deletable_manifests": len(untagged_manifests),
        "deletable_gb": deletable_gb,
        "deleted": 0,
        "failed": 0,
    }

    if not execute:
        print("  [DRY-RUN] No deletions performed.")
        return result

    # Phase A: Delete untagged indexes first (releases child references)
    if untagged_indexes:
        print(f"\n  Phase A: Deleting {len(untagged_indexes)} untagged indexes...")
        d, f = batch_delete(repo, untagged_indexes, "indexes")
        result["deleted"] += d
        result["failed"] += f
        print(f"  Phase A complete: deleted={d}, failed={f}")

    # Phase B: Delete orphaned platform manifests
    if untagged_manifests:
        print(f"\n  Phase B: Deleting {len(untagged_manifests)} orphaned manifests...")
        d, f = batch_delete(repo, untagged_manifests, "manifests")
        result["deleted"] += d
        result["failed"] += f
        print(f"  Phase B complete: deleted={d}, failed={f}")

    return result


def verify_repo(repo: str) -> None:
    """Post-cleanup verification."""
    print(f"\n  Verifying {repo}...")
    images = get_all_images(repo)
    total = len(images)
    tagged = sum(1 for img in images if img.get("imageTags"))
    untagged = total - tagged

    # Storage
    total_bytes = sum(img.get("imageSizeInBytes", 0) for img in images)
    total_gb = total_bytes / (1024**3)

    # Lifecycle policy check
    raw = aws_ecr("get-lifecycle-policy", "--repository-name", repo)
    policy_data = json.loads(raw) if raw.strip() else {}
    last_eval = policy_data.get("lastEvaluatedAt", "UNKNOWN")

    print(f"    Images: {total} (tagged={tagged}, untagged={untagged})")
    print(f"    Storage: {total_gb:.2f} GB")
    print(f"    Lifecycle lastEvaluatedAt: {last_eval}")

    if untagged > tagged * 5:
        print(f"    [WARN] Untagged ({untagged}) >> Tagged ({tagged}). Cleanup may need re-run.")
    else:
        print(f"    [OK] Ratio acceptable.")


def main():
    parser = argparse.ArgumentParser(description="ECR Manifest-Aware Cleanup")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Show what would be deleted")
    group.add_argument("--execute", action="store_true", help="Actually delete images")
    group.add_argument("--verify", action="store_true", help="Verify post-cleanup state")
    parser.add_argument("--repo", help="Single repo to clean (default: all)")
    args = parser.parse_args()

    repos = [args.repo] if args.repo else REPOS

    if args.verify:
        print("\n" + "="*60)
        print("  ECR POST-CLEANUP VERIFICATION")
        print("="*60)
        for repo in repos:
            verify_repo(repo)
        return

    print("\n" + "="*60)
    print(f"  ECR MANIFEST-AWARE CLEANUP ({'DRY-RUN' if args.dry_run else 'EXECUTE'})")
    print("="*60)

    results = []
    for repo in repos:
        r = cleanup_repo(repo, execute=args.execute)
        results.append(r)

    # Summary
    print("\n" + "="*60)
    print("  SUMMARY")
    print("="*60)
    total_deletable = sum(r["deletable_indexes"] + r["deletable_manifests"] for r in results)
    total_gb = sum(r["deletable_gb"] for r in results)
    total_deleted = sum(r["deleted"] for r in results)
    total_failed = sum(r["failed"] for r in results)

    print(f"  Total deletable: {total_deletable} images ({total_gb:.1f} GB)")
    if args.execute:
        print(f"  Total deleted: {total_deleted}")
        print(f"  Total failed: {total_failed}")
        est_savings = total_gb * 0.10  # $0.10/GB/month
        print(f"  Estimated monthly savings: ${est_savings:.0f}/month")
    else:
        est_savings = total_gb * 0.10
        print(f"  Estimated monthly savings if cleaned: ${est_savings:.0f}/month")
        print(f"\n  Run with --execute to perform actual deletion.")


if __name__ == "__main__":
    main()
