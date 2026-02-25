#!/usr/bin/env python3
"""
SSOT 검증 보조: ECR 이미지 digest/아키텍처, CloudWatch 로그 오류 패턴.
PowerShell verify_video_batch_ssot.ps1 에서 호출 (선택). 증거 위주 출력.
Usage: python verify_video_batch_ssot.py --region ap-northeast-2
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta

try:
    import boto3
except ImportError:
    boto3 = None


def _log(msg: str) -> None:
    print(msg)
    sys.stdout.flush()


def _ecr_latest_digest(ecr_client, repo: str, tag: str = "latest") -> str | None:
    try:
        r = ecr_client.batch_get_image(
            repositoryName=repo,
            imageIds=[{"imageTag": tag}],
        )
        if r.get("images") and len(r["images"]) > 0:
            return r["images"][0].get("imageId", {}).get("imageDigest")
    except Exception as e:
        _log(f"  ECR batch_get_image {repo}:{tag} error: {e}")
    return None


def _ecr_manifest_arch(ecr_client, repo: str, tag: str = "latest") -> str:
    """Return arch from manifest (arm64/amd64/unknown). Prefer manifest list."""
    try:
        r = ecr_client.batch_get_image(
            repositoryName=repo,
            imageIds=[{"imageTag": tag}],
        )
        if not r.get("images") or len(r["images"]) == 0:
            return "unknown(no image)"
        img = r["images"][0]
        manifest = img.get("imageManifest")
        if not manifest:
            return "unknown(no manifest)"
        if isinstance(manifest, bytes):
            manifest = manifest.decode("utf-8")
        data = json.loads(manifest)
        # Manifest list (multi-arch)
        if data.get("manifests"):
            archs = []
            for m in data["manifests"]:
                plat = m.get("platform", {})
                arch = plat.get("architecture", "unknown")
                archs.append(arch)
            return ",".join(archs) if archs else "unknown"
        # Single image
        return "unknown(single)"
    except Exception as e:
        return f"unknown({e})"


def _jobdef_image(region: str) -> tuple[str | None, str | None]:
    """Get Video JobDef image (repo:tag or digest). Returns (image_uri, digest if in image)."""
    try:
        client = boto3.client("batch", region_name=region)
        r = client.describe_job_definitions(
            jobDefinitionName="academy-video-batch-jobdef",
            status="ACTIVE",
        )
        if not r.get("jobDefinitions"):
            return None, None
        jd = r["jobDefinitions"][0]
        img = jd.get("containerProperties", {}).get("image")
        if not img:
            return None, None
        digest = None
        if "@sha256:" in img:
            digest = img.split("@sha256:")[-1].strip()
        return img, digest
    except Exception as e:
        _log(f"  JobDef describe error: {e}")
        return None, None


def _ecr_digest_from_uri(ecr_client, image_uri: str) -> str | None:
    """Resolve digest for ECR URI (account.dkr.ecr.region.amazonaws.com/repo:tag or @sha256:xxx)."""
    if "@sha256:" in image_uri:
        return image_uri.split("@sha256:")[-1].strip()
    parts = image_uri.split("/", 1)
    if len(parts) != 2:
        return None
    repo_tag = parts[1]
    if ":" in repo_tag:
        repo, tag = repo_tag.rsplit(":", 1)
    else:
        repo, tag = repo_tag, "latest"
    return _ecr_latest_digest(ecr_client, repo, tag)


def run_ecr_checks(region: str) -> None:
    if not boto3:
        _log("  (boto3 not installed; skip ECR/CloudWatch)")
        return
    _log("--- ECR image vs JobDefinition ---")
    ecr = boto3.client("ecr", region_name=region)
    repo = "academy-video-worker"
    tag = "latest"
    latest_digest = _ecr_latest_digest(ecr, repo, tag)
    arch = _ecr_manifest_arch(ecr, repo, tag)
    _log(f"  ECR {repo}:{tag} digest={latest_digest or 'N/A'} arch={arch}")

    jd_image, jd_digest = _jobdef_image(region)
    if jd_image:
        _log(f"  JobDef image={jd_image}")
        if latest_digest and jd_digest:
            if jd_digest == latest_digest:
                _log("  evidence: JobDefinition digest matches ECR :latest")
            else:
                _log("  WARN: JobDefinition digest does not match ECR :latest (redeploy or pin)")
        elif jd_digest:
            _log("  evidence: JobDefinition uses pinned digest")
        else:
            jd_resolved = _ecr_digest_from_uri(ecr, jd_image)
            if jd_resolved and latest_digest and jd_resolved == latest_digest:
                _log("  evidence: JobDef image resolves to same as ECR :latest")
            else:
                _log("  (could not compare digest; tag may be :latest)")


def run_cloudwatch_log_checks(region: str, hours: int = 6) -> None:
    if not boto3:
        return
    _log("--- CloudWatch log errors (last %d h) ---" % hours)
    logs = boto3.client("logs", region_name=region)
    patterns = [
        "CannotPullContainerError",
        "manifest unknown",
        "exec format",
        "ResourceInitializationError",
    ]
    since = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)
    groups = ["/aws/batch/academy-video-worker", "/aws/batch/academy-video-ops-worker"]
    found_any = False
    for log_group in groups:
        try:
            streams = logs.describe_log_streams(
                logGroupName=log_group,
                orderBy="LastEventTime",
                descending=True,
                limit=20,
            )
            for s in streams.get("logStreams", [])[:10]:
                name = s["logStreamName"]
                try:
                    events = logs.filter_log_events(
                        logGroupName=log_group,
                        logStreamNames=[name],
                        startTime=since,
                    )
                    for ev in events.get("events", []):
                        msg = ev.get("message", "")
                        for p in patterns:
                            if p in msg:
                                _log("  [%s] %s: ...%s..." % (log_group, name, p))
                                found_any = True
                                break
                except Exception:
                    pass
        except Exception as e:
            _log("  %s: %s" % (log_group, e))
    if not found_any:
        _log("  no matching error patterns in last %d h" % hours)


def main() -> None:
    ap = argparse.ArgumentParser(description="SSOT verify supplement: ECR + CloudWatch")
    ap.add_argument("--region", default="ap-northeast-2")
    ap.add_argument("--hours", type=int, default=6, help="CloudWatch search window (hours)")
    args = ap.parse_args()
    run_ecr_checks(args.region)
    run_cloudwatch_log_checks(args.region, args.hours)


if __name__ == "__main__":
    main()
