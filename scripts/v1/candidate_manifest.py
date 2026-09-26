"""Validate provenance-bound, expiring six-image candidates before promotion."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY = "guswls3028-art/academy-backend"
WORKFLOW = ".github/workflows/candidate-prepare.yml"
IMAGE_NAMES = ("academy-base", "academy-api", "academy-video-worker",
               "academy-messaging-worker", "academy-ai-worker-cpu", "academy-tools-worker")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
SHA = re.compile(r"[0-9a-f]{40}")
ARTIFACT_NAME = "academy-candidate"
MAX_BYTES = 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(document, *, sha=None, now=None, promotion=True):
    now = int(time.time()) if now is None else now
    require(isinstance(document, dict), "Candidate must be an object")
    require(document.get("schemaVersion") == 1 and document.get("status") == "candidate"
            and document.get("complete") is False, "Invalid candidate status")
    source = document.get("gitSha", "")
    require(isinstance(source, str) and SHA.fullmatch(source), "Invalid candidate source SHA")
    if sha is not None:
        require(source == sha, "Candidate is not exact current main")
    p = document.get("preparation", {})
    require(p.get("repository") == REPOSITORY and p.get("workflow") == WORKFLOW,
            "Unexpected preparation origin")
    require(p.get("mode") in ("isolated-qa", "production"), "Invalid preparation mode")
    if promotion:
        require(p["mode"] == "production" and p.get("ref") == "refs/heads/main"
                and p.get("controllerSha") == source, "PR QA artifact cannot be promoted")
    for key in ("runId", "runAttempt", "createdAt", "expiresAt"):
        require(type(p.get(key)) is int and p[key] > 0, "Invalid preparation integer")
    require(p["createdAt"] <= now < p["expiresAt"]
            and p["expiresAt"] - p["createdAt"] == 30 * 86400, "Candidate expired or invalid retention")
    require(document.get("releaseImageTag") == f"sha-{source}-run-{p['runId']}-{p['runAttempt']}",
            "Candidate release identity mismatch")
    require(p.get("developmentGate") == "pass" and p.get("cleanupZero") is True,
            "Development gate or cleanup proof missing")
    require(p.get("providerQa") in ("unavailable", "pending", "pass"), "Invalid provider QA state")
    require(p.get("models") == {"text": "gemini-2.5-flash-lite", "vision": "gemini-2.5-flash"},
            "Provider model pins changed")
    images = document.get("images", {})
    require(set(images) == set(IMAGE_NAMES), "Candidate needs exactly six image roles")
    for role, image in images.items():
        expected_repo = role if p["mode"] == "production" else role.replace("academy-", "academy-qa-", 1)
        require(isinstance(image, dict) and image.get("repository") == expected_repo,
                "Image repository crosses candidate boundary")
        require(isinstance(image.get("digest"), str) and DIGEST.fullmatch(image["digest"]),
                "Invalid immutable image digest")
        require(image.get("tag") == document["releaseImageTag"], "Image tag identity differs")
    return document


def github(path):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        result = subprocess.run(["gh", "api", path, "--hostname", "github.com"],
                                capture_output=True, text=True, check=False)
        if result.returncode:
            if "(HTTP 404)" in result.stderr:
                raise urllib.error.HTTPError("https://api.github.com/" + path, 404, "Not Found", {}, None)
            raise ValueError("Configured GitHub read authentication failed")
        return json.loads(result.stdout)
    req = urllib.request.Request("https://api.github.com/" + path, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def archive(artifact_id):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip",
        headers={"Authorization": "Bearer " + str(token)})
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(req, timeout=30)
    except urllib.error.HTTPError as response:
        require(response.code == 302, "Artifact download did not return a signed redirect")
        location = response.headers.get("Location", "")
    else:
        raise ValueError("Unexpected artifact archive response")
    require(location.startswith("https://"), "Artifact archive URL must be HTTPS")
    # No GitHub Authorization header is sent to artifact storage.
    with urllib.request.urlopen(location, timeout=60) as response:
        data = response.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES, "Candidate archive exceeds bounded size")
    return data


def verify_image_readback(document):
    """Production executors use immutable tags as well as digests; bind both."""
    for role, image in document["images"].items():
        def read(*args):
            result = subprocess.run(["aws", "ecr", *args, "--region", "ap-northeast-2", "--output", "json"],
                                    capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        repos = read("describe-repositories", "--repository-names", role)["repositories"]
        require(len(repos) == 1 and repos[0]["registryId"] == "809466760795", "Unexpected image account")
        repository = repos[0]
        mode = repository.get("imageTagMutability")
        require(mode == "IMMUTABLE" or (
            mode == "IMMUTABLE_WITH_EXCLUSION" and repository.get("imageTagMutabilityExclusionFilters") ==
            [{"filterType": "WILDCARD", "filter": "latest"}]), "Release tag is not immutable")
        details = read("describe-images", "--repository-name", role,
                       "--image-ids", "imageTag=" + image["tag"])["imageDetails"]
        require(len(details) == 1 and details[0]["imageDigest"] == image["digest"],
                "Release tag no longer resolves to the prepared digest")


def restore(artifact_id, expected_digest, expected_sha, output):
    require(re.fullmatch(r"[1-9][0-9]*", str(artifact_id)), "Invalid artifact ID")
    require(DIGEST.fullmatch(expected_digest), "Artifact digest must be sha256")
    main = github(f"repos/{REPOSITORY}/git/ref/heads/main")["object"]["sha"]
    require(main == expected_sha, "Promotion checkout is not current remote main")
    info = github(f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}")
    match = re.fullmatch(re.escape(ARTIFACT_NAME) + r"-([1-9][0-9]*)-([1-9][0-9]*)", info.get("name", ""))
    require(match and info.get("expired") is False
            and info.get("digest") == expected_digest, "Artifact metadata/digest mismatch")
    require(0 < info.get("size_in_bytes", 0) <= MAX_BYTES, "Invalid artifact size")
    run_id = info.get("workflow_run", {}).get("id")
    require(type(run_id) is int and int(match[1]) == run_id, "Artifact name/run identity differs")
    attempt = int(match[2])
    run = github(f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}")
    require(run.get("path") == WORKFLOW and run.get("event") == "workflow_dispatch"
            and run.get("head_branch") == "main" and run.get("head_sha") == expected_sha
            and run.get("conclusion") == "success" and run.get("status") == "completed"
            and run.get("repository", {}).get("full_name") == REPOSITORY,
            "Artifact is not from the protected successful main preparation workflow")
    raw = archive(artifact_id)
    require("sha256:" + hashlib.sha256(raw).hexdigest() == expected_digest, "Archive hash mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        require(zipped.namelist() == ["release-manifest.candidate.json"], "Unexpected artifact members")
        member = zipped.getinfo("release-manifest.candidate.json")
        require(member.file_size <= MAX_BYTES, "Candidate manifest is too large")
        document = json.loads(zipped.read(member))
    validate(document, sha=expected_sha)
    p = document["preparation"]
    require(p["runId"] == run_id and p["runAttempt"] == run.get("run_attempt"),
            "Artifact run/attempt provenance differs")
    verify_image_readback(document)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n")
    return document


def create(images_path, mode, source, controller, run_id, attempt, output, evidence_path, provider=False):
    now = int(time.time())
    images = json.loads(images_path.read_text())
    evidence = json.loads(evidence_path.read_text())
    release = f"sha-{source}-run-{run_id}-{attempt}"
    require(evidence.get("release_id") == release and evidence.get("stage") == "development"
            and evidence.get("syntheticSmokeCleanupZero") is True, "Development evidence mismatch")
    for key, role in (("api", "academy-api"), ("tools", "academy-tools-worker"),
                      ("ai", "academy-ai-worker-cpu"), ("messaging", "academy-messaging-worker")):
        image = images[role]
        uri = "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/" + image["repository"] + "@" + image["digest"]
        require(evidence.get(key) == uri, "Development evidence does not cover the candidate digest")

    document = {
        "schemaVersion": 1, "status": "candidate", "complete": False,
        "generatedAt": datetime.now(timezone.utc).isoformat(), "gitSha": source,
        "releaseImageTag": f"sha-{source}-run-{run_id}-{attempt}", "images": images,
        "preparation": {
            "repository": REPOSITORY, "workflow": WORKFLOW, "ref": "refs/heads/main",
            "mode": mode, "controllerSha": controller, "runId": run_id, "runAttempt": attempt,
            "createdAt": now, "expiresAt": now + 30 * 86400,
            "developmentGate": "pass", "cleanupZero": True,
            "providerQa": "pending" if provider else "unavailable",
            "models": {"text": "gemini-2.5-flash-lite", "vision": "gemini-2.5-flash"},
        },
    }
    validate(document, promotion=mode == "production", now=now)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + "\n")
    return document


def retain(ecr, document):
    """Register all candidate digests under the existing shared mutation lock."""
    validate(document, promotion=False)
    expires = document["preparation"]["expiresAt"]
    for image in document["images"].values():
        repo, digest = image["repository"], image["digest"]
        # Native expiration is independent of our cleanup script; require it absent.
        try:
            ecr.get_lifecycle_policy(repositoryName=repo)
        except ecr.exceptions.LifecyclePolicyNotFoundException:
            pass
        else:
            raise ValueError("Native ECR lifecycle policy may violate candidate retention")
        result = ecr.batch_get_image(repositoryName=repo, imageIds=[{"imageDigest": digest}])
        require(not result.get("failures") and len(result.get("images", [])) == 1,
                "Candidate image is unavailable")
        ecr.put_image(repositoryName=repo, imageManifest=result["images"][0]["imageManifest"],
                      imageTag=f"candidate-until-{expires}-{document['preparation']['runId']}-{document['preparation']['runAttempt']}")
        readback = ecr.describe_images(repositoryName=repo, imageIds=[{"imageDigest": digest}])
        require(any(f"candidate-until-{expires}-{document['preparation']['runId']}-{document['preparation']['runAttempt']}" in x.get("imageTags", [])
                    for x in readback.get("imageDetails", [])), "Candidate retention readback failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("restore")
    p.add_argument("--artifact-id", required=True)
    p.add_argument("--digest", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("create")
    p.add_argument("--images", type=Path, required=True)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--mode", choices=("isolated-qa", "production"), required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--controller-sha", required=True)
    p.add_argument("--run-id", type=int, required=True)
    p.add_argument("--attempt", type=int, required=True)
    p.add_argument("--provider", action="store_true")
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("retain")
    p.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "restore":
            restore(args.artifact_id, args.digest, args.sha, args.output)
        elif args.command == "create":
            create(args.images, args.mode, args.sha, args.controller_sha, args.run_id,
                   args.attempt, args.output, args.evidence, args.provider)
        else:
            import boto3
            subprocess.run(["python3", str(Path(__file__).with_name("deployment_lock.py")), "renew",
                            "--owner", os.environ["ACADEMY_DEPLOY_LOCK_OWNER"], "--ttl-seconds", "10800"], check=True)
            retain(boto3.client("ecr", region_name="ap-northeast-2"), json.loads(args.manifest.read_text()))
        print("CANDIDATE_MANIFEST_OK")
    except Exception:
        parser.exit(2, "CANDIDATE_MANIFEST_BLOCKED: provenance, retention or immutable image verification failed\n")


if __name__ == "__main__":
    main()
