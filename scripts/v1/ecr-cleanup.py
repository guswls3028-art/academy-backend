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
import atexit
import base64
import json
import os
import re
import subprocess
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REGION = "ap-northeast-2"
ACCOUNT_ID = "809466760795"

REPOS = [
    "academy-base",
    "academy-api",
    "academy-ai-worker-cpu",
    "academy-messaging-worker",
    "academy-tools-worker",
    "academy-video-worker",
]

ASG_REPOSITORIES = {
    "academy-v1-api-asg": "academy-api",
    "academy-v1-messaging-worker-asg": "academy-messaging-worker",
    "academy-v1-ai-worker-asg": "academy-ai-worker-cpu",
    "academy-v1-tools-worker-asg": "academy-tools-worker",
}

ASG_CONTAINERS = {
    "academy-v1-api-asg": "academy-api",
    "academy-v1-messaging-worker-asg": "academy-messaging-worker",
    "academy-v1-ai-worker-asg": "academy-ai-worker-cpu",
    "academy-v1-tools-worker-asg": "academy-tools-worker",
}

# OCI manifest types
TYPE_INDEX = "application/vnd.oci.image.index.v1+json"
TYPE_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
TYPE_DOCKER_LIST = "application/vnd.docker.distribution.manifest.list.v2+json"
TYPE_DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"

INDEX_TYPES = {TYPE_INDEX, TYPE_DOCKER_LIST}
MANIFEST_TYPES = {TYPE_MANIFEST, TYPE_DOCKER_MANIFEST}

# Tags that must never be deleted
ALWAYS_KEEP_TAGS = {"latest"}

REQUIRED_VIDEO_JOB_DEFINITIONS = {
    "academy-v1-video-batch-jobdef",
    "academy-v1-video-ops-scanstuck",
    "academy-v1-video-ops-reconcile",
    "academy-v1-video-ops-netprobe",
    "academy-v1-video-ops-enqueue-uploaded",
    "academy-v1-video-ops-purge-raw",
    "academy-v1-video-ops-detect-stuck",
    "academy-v1-video-ops-recover-dead",
}
RELEASE_MANIFEST = Path(__file__).resolve().parents[2] / "docs/reports/release-manifest.latest.json"
LOCK_HELPER = Path(__file__).with_name("deployment_lock.py")


_NONFATAL_ACTIONS = {"get-lifecycle-policy"}  # missing IAM perm tolerated for verify-only display


def require_cleanup_lock() -> None:
    """Join a workflow lock or acquire/release one for direct execution."""
    owner = os.environ.get("ACADEMY_DEPLOY_LOCK_OWNER")
    owned_here = not owner
    owner = owner or f"ecr-cleanup:{os.getpid()}:{uuid.uuid4().hex}"
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


def aws_ecr(*args) -> str:
    """Run aws ecr command and return stdout. Hard-fails on error (except verify-only ops)."""
    cmd = ["aws", "ecr", "--region", REGION] + list(args) + ["--output", "json"]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        action = args[0] if args else ""
        msg = r.stderr.strip()
        print(f"  [ERROR] aws ecr {action}: {msg}", file=sys.stderr)
        if action in _NONFATAL_ACTIONS:
            return "{}"
        # Hard fail: weekly cron must surface real errors instead of reporting deleted=0 success.
        sys.exit(2)
    return r.stdout


def aws_service(service: str, *args) -> dict:
    """Run a read-only AWS inventory call and hard-fail cleanup on uncertainty."""
    cmd = ["aws", service, "--region", REGION] + list(args) + ["--output", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        action = args[0] if args else ""
        print(f"  [ERROR] aws {service} {action}: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        print(f"  [ERROR] invalid aws {service} JSON: {exc}", file=sys.stderr)
        sys.exit(2)


def wait_for_ssm_command(command_id: str, instance_id: str) -> None:
    """Wait for an SSM command; final status is validated by the caller."""
    command = [
        "aws", "ssm", "wait", "command-executed",
        "--command-id", command_id,
        "--instance-id", instance_id,
        "--region", REGION,
    ]
    # The waiter returns nonzero both for terminal command failure and timeout.
    # Always read the command invocation afterward for the authoritative status.
    subprocess.run(command, capture_output=True, text=True, check=False)


ECR_IMAGE_RE = re.compile(
    rf"^{ACCOUNT_ID}\.dkr\.ecr\.{re.escape(REGION)}\.amazonaws\.com/"
    r"(?P<repo>[a-z0-9][a-z0-9._/-]*)(?:(?:@(?P<digest>sha256:[0-9a-f]{64}))|(?::(?P<tag>[^:@]+)))$",
    re.IGNORECASE,
)


def resolve_image_reference(image: str) -> tuple[str, str] | None:
    """Return an exact known-repository digest for a runtime image reference."""
    match = ECR_IMAGE_RE.fullmatch(image.strip())
    if not match:
        return None
    repo = match.group("repo")
    if repo not in REPOS:
        return None
    digest = match.group("digest")
    if digest:
        return repo, digest.lower()
    tag = match.group("tag")
    raw = aws_ecr(
        "describe-images", "--repository-name", repo,
        "--image-ids", f"imageTag={tag}",
    )
    details = json.loads(raw).get("imageDetails", [])
    resolved = details[0].get("imageDigest", "") if details else ""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", resolved):
        print(f"  [ERROR] cannot resolve active runtime tag: {image}", file=sys.stderr)
        sys.exit(2)
    return repo, resolved


def _launch_template_references(group: dict) -> set[tuple[str, str, str]]:
    refs: set[tuple[str, str, str]] = set()

    def add(ref: dict | None):
        if not ref:
            return
        template_id = str(ref.get("LaunchTemplateId", ""))
        template_name = str(ref.get("LaunchTemplateName", ""))
        version = str(ref.get("Version", ""))
        if template_id or template_name:
            if not version:
                print("  [ERROR] ASG launch template reference has no version", file=sys.stderr)
                sys.exit(2)
            refs.add((template_id, template_name, version))

    add(group.get("LaunchTemplate"))
    mixed = group.get("MixedInstancesPolicy", {}).get("LaunchTemplate", {})
    add(mixed.get("LaunchTemplateSpecification"))
    for override in mixed.get("Overrides", []):
        add(override.get("LaunchTemplateSpecification"))
    # During an instance refresh, running instances may still use older LT
    # versions. Protect every version currently reported by the ASG.
    for instance in group.get("Instances", []):
        add(instance.get("LaunchTemplate"))
    return refs


def collect_actual_instance_runtime_digest(
    instance_id: str, expected_repo: str, container_name: str
) -> str:
    """Read the exact RepoDigest used by one running ASG container via SSM."""
    remote_command = (
        "set -e; "
        f"IMAGE_ID=$(docker inspect --format '{{{{.Image}}}}' '{container_name}'); "
        "docker image inspect --format "
        "'{{range .RepoDigests}}{{println .}}{{end}}' \"$IMAGE_ID\""
    )
    sent = aws_service(
        "ssm", "send-command",
        "--instance-ids", instance_id,
        "--document-name", "AWS-RunShellScript",
        "--parameters", json.dumps(
            {"commands": [remote_command], "executionTimeout": ["120"]}
        ),
        "--timeout-seconds", "180",
    )
    command_id = str(sent.get("Command", {}).get("CommandId", ""))
    if not command_id:
        print(
            f"  [ERROR] SSM returned no command id for {instance_id}/{container_name}",
            file=sys.stderr,
        )
        sys.exit(2)
    wait_for_ssm_command(command_id, instance_id)
    invocation = aws_service(
        "ssm", "get-command-invocation",
        "--command-id", command_id,
        "--instance-id", instance_id,
    )
    if invocation.get("Status") != "Success":
        print(
            f"  [ERROR] cannot inventory actual runtime digest on {instance_id}: "
            f"status={invocation.get('Status')} stderr={invocation.get('StandardErrorContent', '')}",
            file=sys.stderr,
        )
        sys.exit(2)
    actual_refs = {
        resolved
        for line in str(invocation.get("StandardOutputContent", "")).splitlines()
        if (resolved := resolve_image_reference(line.strip())) is not None
        and resolved[0] == expected_repo
    }
    if len(actual_refs) != 1:
        print(
            f"  [ERROR] {instance_id}/{container_name} must report exactly one "
            f"{expected_repo} RepoDigest; actual={sorted(actual_refs)}",
            file=sys.stderr,
        )
        sys.exit(2)
    return next(iter(actual_refs))[1]


def collect_runtime_protected_digests() -> dict[str, set[str]]:
    """Inventory every digest referenced by ASG LT versions and ACTIVE Batch jobdefs."""
    protected = {repo: set() for repo in REPOS}
    groups = aws_service("autoscaling", "describe-auto-scaling-groups").get("AutoScalingGroups", [])
    groups_by_name = {group.get("AutoScalingGroupName", ""): group for group in groups}
    missing_groups = sorted(set(ASG_REPOSITORIES) - set(groups_by_name))
    if missing_groups:
        print(f"  [ERROR] required runtime ASGs missing: {', '.join(missing_groups)}", file=sys.stderr)
        sys.exit(2)

    for asg_name, expected_repo in ASG_REPOSITORIES.items():
        group = groups_by_name[asg_name]
        refs = _launch_template_references(group)
        if not refs:
            print(f"  [ERROR] required ASG has no Launch Template references: {asg_name}", file=sys.stderr)
            sys.exit(2)
        for template_id, template_name, version in sorted(refs):
            args = ["describe-launch-template-versions"]
            if template_id:
                args += ["--launch-template-id", template_id]
            else:
                args += ["--launch-template-name", template_name]
            args += ["--versions", version]
            versions = aws_service("ec2", *args).get("LaunchTemplateVersions", [])
            if len(versions) != 1:
                print(
                    f"  [ERROR] launch template reference did not resolve exactly once: "
                    f"id={template_id} name={template_name} version={version}",
                    file=sys.stderr,
                )
                sys.exit(2)
            encoded = versions[0].get("LaunchTemplateData", {}).get("UserData", "")
            if not encoded:
                print(f"  [ERROR] required ASG LT userdata is empty: {asg_name}/{version}", file=sys.stderr)
                sys.exit(2)
            try:
                userdata = base64.b64decode(encoded, validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                print(f"  [ERROR] invalid launch template userdata: {exc}", file=sys.stderr)
                sys.exit(2)
            # User data legitimately repeats the same immutable URI in pull,
            # run, and error-log commands.  Count unique runtime references,
            # not textual occurrences, while still rejecting two distinct
            # images for the expected repository.
            resolved_refs: set[tuple[str, str]] = set()
            for token in re.findall(r"[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[^\s'\"\\]+", userdata):
                resolved = resolve_image_reference(token.rstrip(");,"))
                if resolved:
                    resolved_refs.add(resolved)
            expected = sorted(
                (repo, digest) for repo, digest in resolved_refs if repo == expected_repo
            )
            if len(expected) != 1:
                print(
                    f"  [ERROR] {asg_name} LT version {version} must reference exactly one "
                    f"{expected_repo} image; actual={sorted(resolved_refs)}",
                    file=sys.stderr,
                )
                sys.exit(2)
            protected[expected_repo].add(expected[0][1])

        desired = int(group.get("DesiredCapacity", 0))
        in_service = [
            instance for instance in group.get("Instances", [])
            if instance.get("LifecycleState") == "InService"
        ]
        if len(in_service) != desired:
            print(
                f"  [ERROR] {asg_name} actual runtime inventory requires "
                f"InService={desired}; found={len(in_service)}",
                file=sys.stderr,
            )
            sys.exit(2)
        for instance in in_service:
            instance_id = str(instance.get("InstanceId", ""))
            if not instance_id:
                print(f"  [ERROR] {asg_name} has an InService instance without an id", file=sys.stderr)
                sys.exit(2)
            actual_digest = collect_actual_instance_runtime_digest(
                instance_id,
                expected_repo,
                ASG_CONTAINERS[asg_name],
            )
            protected[expected_repo].add(actual_digest)

    definitions = aws_service(
        "batch", "describe-job-definitions", "--status", "ACTIVE"
    ).get("jobDefinitions", [])
    by_name: dict[str, list[dict]] = defaultdict(list)
    for definition in definitions:
        by_name[str(definition.get("jobDefinitionName", ""))].append(definition)
        image = str(definition.get("containerProperties", {}).get("image", ""))
        resolved = resolve_image_reference(image)
        if resolved:
            repo, digest = resolved
            protected[repo].add(digest)

    missing = sorted(REQUIRED_VIDEO_JOB_DEFINITIONS - set(by_name))
    if missing:
        print(f"  [ERROR] required ACTIVE video job definitions missing: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)
    latest_video_digests: set[str] = set()
    for name in sorted(REQUIRED_VIDEO_JOB_DEFINITIONS):
        latest = max(by_name[name], key=lambda item: int(item.get("revision", 0)))
        image = str(latest.get("containerProperties", {}).get("image", ""))
        exact = ECR_IMAGE_RE.fullmatch(image.strip())
        resolved = resolve_image_reference(image)
        if not exact or not exact.group("digest") or not resolved or resolved[0] != "academy-video-worker":
            print(f"  [ERROR] {name} latest ACTIVE revision is not pinned to academy-video-worker: {image}", file=sys.stderr)
            sys.exit(2)
        latest_video_digests.add(resolved[1])
    if len(latest_video_digests) != 1:
        print(f"  [ERROR] required Video job definitions disagree on latest digest: {sorted(latest_video_digests)}", file=sys.stderr)
        sys.exit(2)

    protected_from_manifest = collect_release_manifest_digests()
    for repo, digest in protected_from_manifest.items():
        protected[repo].add(digest)

    return protected


def collect_release_manifest_digests() -> dict[str, str]:
    """Fail closed and protect every digest in the last successful complete release."""
    try:
        manifest = json.loads(RELEASE_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  [ERROR] cannot read successful release manifest {RELEASE_MANIFEST}: {exc}", file=sys.stderr)
        sys.exit(2)
    if manifest.get("schemaVersion") != 1 or manifest.get("status") != "successful" or manifest.get("complete") is not True:
        print("  [ERROR] release manifest must be schemaVersion=1, complete=true, status=successful", file=sys.stderr)
        sys.exit(2)
    images = manifest.get("images")
    if not isinstance(images, dict) or set(images) != set(REPOS):
        print(f"  [ERROR] release manifest must contain exactly: {', '.join(REPOS)}", file=sys.stderr)
        sys.exit(2)
    result: dict[str, str] = {}
    for repo in REPOS:
        entry = images.get(repo)
        digest = entry.get("digest", "") if isinstance(entry, dict) else ""
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            print(f"  [ERROR] release manifest has invalid digest for {repo}: {digest}", file=sys.stderr)
            sys.exit(2)
        result[repo] = digest
    return result


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
    images: list[dict], keep: int, repo: str, runtime_digests: set[str] | None = None
) -> tuple[set[str], list[dict]]:
    """
    Determine which digests to protect.

    Protected:
    - 'latest' tag and its children (always)
    - Newest `keep` sha- tagged images and their children
    - Every digest referenced by an ASG LT/running instance LT or ACTIVE Batch jobdef

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

    images_by_digest = {img.get("imageDigest", ""): img for img in images}
    for digest in sorted(runtime_digests or set()):
        image = images_by_digest.get(digest)
        if not image:
            print(f"  [ERROR] active runtime digest is missing from {repo}: {digest}", file=sys.stderr)
            sys.exit(2)
        protected.add(digest)
        if image.get("imageManifestMediaType", "") in INDEX_TYPES:
            protected.update(resolve_child_digests(repo, digest))

    # Log
    sha_tags_kept = []
    for img in kept_sha:
        tags = [t for t in img.get("imageTags", []) if t.startswith("sha-")]
        sha_tags_kept.extend(tags)

    sha_tags_deleted = []
    for img in deletable_sha:
        tags = [t for t in img.get("imageTags", []) if t.startswith("sha-")]
        sha_tags_deleted.extend(tags)

    print(
        f"  Protected: latest + {len(kept_sha)} newest sha- + "
        f"{len(runtime_digests or set())} runtime digest(s) = {len(protected)} digests"
    )
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
        if digest in protected:
            continue
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


def cleanup_repo(
    repo: str,
    keep: int,
    execute: bool,
    runtime_digests: set[str] | None = None,
) -> dict:
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
    protected, deletable_tagged = identify_protected_set(
        images, keep, repo, runtime_digests=runtime_digests
    )

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

    repository = aws_service(
        "ecr", "describe-repositories", "--repository-names", repo
    ).get("repositories", [{}])[0]
    exclusions = repository.get("imageTagMutabilityExclusionFilters", [])
    mutability_ok = (
        repository.get("imageTagMutability") == "IMMUTABLE_WITH_EXCLUSION"
        and exclusions == [{"filterType": "WILDCARD", "filter": "latest"}]
    )
    status = "OK" if untagged <= tagged * 2 and mutability_ok else "WARN"
    icon = "[OK]" if status == "OK" else "[WARN]"

    print(f"    Images: {total} (tagged={tagged}, untagged={untagged})")
    print(f"    Storage: {total_gb:.2f} GB")
    print(f"    Lifecycle lastEvaluatedAt: {last_eval}")
    print(f"    Tag mutability: {'OK (latest-only mutable)' if mutability_ok else 'INVALID'}")
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
            sys.exit(2)
        else:
            print("  All repos healthy.")
        return

    mode = "DRY-RUN" if args.dry_run else "EXECUTE"
    print(f"\n{'=' * 60}")
    print(f"  ECR MANIFEST-AWARE CLEANUP ({mode})")
    print(f"  Keep: latest + newest {args.keep} sha- tags per repo")
    print(f"{'=' * 60}")

    if args.execute:
        require_cleanup_lock()
        print("\n  [!] EXECUTE mode: images will be permanently deleted.")
        print("     Press Ctrl+C within 5 seconds to abort...")
        import time
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n  Aborted.")
            sys.exit(1)

    print("\n  Inventorying active ASG/Batch runtime digests (fail closed)...")
    runtime_protected = collect_runtime_protected_digests()
    for repo in repos:
        print(f"  {repo}: {len(runtime_protected[repo])} active runtime digest(s)")

    results = []
    for repo in repos:
        r = cleanup_repo(
            repo,
            keep=args.keep,
            execute=args.execute,
            runtime_digests=runtime_protected[repo],
        )
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
            print("  [ERROR] Some deletions failed. Run --verify, then --execute again.")
            sys.exit(2)
        else:
            print("  Run --verify to confirm cleanup.")
    else:
        print(f"  Est. monthly savings: ${total_gb * 0.10:.2f}/mo")
        print(f"\n  Run with --execute to perform deletion.")


if __name__ == "__main__":
    main()
