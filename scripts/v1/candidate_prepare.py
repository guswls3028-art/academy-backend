"""Trusted controller for isolated PR QA and exact-main candidate image builds."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from candidate_manifest import IMAGE_NAMES, REPOSITORY, SHA, github, require

REGISTRY = "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com"
DOCKERFILES = {
    "academy-base": "docker/Dockerfile.base", "academy-api": "docker/api/Dockerfile",
    "academy-video-worker": "docker/video-worker/Dockerfile",
    "academy-messaging-worker": "docker/messaging-worker/Dockerfile",
    "academy-ai-worker-cpu": "docker/ai-worker-cpu/Dockerfile",
    "academy-tools-worker": "docker/tools-worker/Dockerfile",
}


def resolve(mode, source, controller, pr):
    require(SHA.fullmatch(source) and SHA.fullmatch(controller), "Full source/controller SHA required")
    main = github(f"repos/{REPOSITORY}/git/ref/heads/main")["object"]["sha"]
    require(controller == main, "Controller must be exact remote main")
    if mode == "production":
        require(source == main and not pr, "Production candidate requires exact main and no PR")
    else:
        require(re.fullmatch(r"[1-9][0-9]*", pr or ""), "PR number required for isolated QA")
        record = github(f"repos/{REPOSITORY}/pulls/{pr}")
        require(record.get("state") == "open" and record.get("base", {}).get("ref") == "main"
                and record.get("head", {}).get("sha") == source
                and record.get("head", {}).get("repo", {}).get("full_name") == REPOSITORY,
                "Isolated QA must bind the exact open same-repository PR head")
    return source


def verify_provider_source(source):
    """Do not inject a key into an adapter that sends it in a URL."""
    source = (source / "academy/adapters/ai/detection/vlm_fallback.py").read_text()
    require("x-goog-api-key" in source and "?key=" not in source,
            "Provider QA requires the reviewed header-auth security change")
    require("gemini-2.5-flash-lite" in source and "gemini-2.5-flash" in source,
            "Reviewed Gemini model pins are missing")


def build(ecr, source, mode, release, output):
    require(re.fullmatch(r"sha-[0-9a-f]{40}-run-[1-9][0-9]*-[1-9][0-9]*", release),
            "Invalid immutable build tag")
    images = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    for role in IMAGE_NAMES:
        repo = role if mode == "production" else role.replace("academy-", "academy-qa-", 1)
        repository = ecr.describe_repositories(repositoryNames=[repo])["repositories"][0]
        require(repository["registryId"] == "809466760795", "Unexpected ECR account")
        require(repository["imageTagMutability"] in ("IMMUTABLE", "IMMUTABLE_WITH_EXCLUSION"),
                "Candidate repository permits mutable image tags")
        if repository["imageTagMutability"] == "IMMUTABLE_WITH_EXCLUSION":
            require(repository.get("imageTagMutabilityExclusionFilters") ==
                    [{"filterType": "WILDCARD", "filter": "latest"}], "Unexpected mutable tag exclusion")
        try:
            ecr.get_lifecycle_policy(repositoryName=repo)
        except ecr.exceptions.LifecyclePolicyNotFoundException:
            pass
        else:
            raise ValueError("Native lifecycle policy conflicts with candidate retention")
        meta = output.parent / (repo + ".build.json")
        command = ["docker", "buildx", "build", "--push", "--platform", "linux/arm64",
                   "--provenance=false", "--sbom=false", "--file", str(source / DOCKERFILES[role]),
                   "--tag", f"{REGISTRY}/{repo}:{release}", "--metadata-file", str(meta)]
        if role == "academy-base":
            command += ["--build-arg", "APT_REFRESH_TOKEN=" + release]
        else:
            base = images["academy-base"]
            command += ["--build-arg", f"BASE_IMAGE={REGISTRY}/{base['repository']}@{base['digest']}"]
        subprocess.run(command + [str(source)], check=True)
        digest = json.loads(meta.read_text())["containerimage.digest"]
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", digest), "Build returned invalid digest")
        actual = ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": release}])["imageDetails"]
        require(len(actual) == 1 and actual[0]["imageDigest"] == digest, "Pushed image digest readback mismatch")
        images[role] = {"repository": repo, "digest": digest, "tag": release, "source": "built"}
        output.write_text(json.dumps(images, indent=2) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    q = sub.add_parser("source")
    q.add_argument("--mode", choices=("isolated-qa", "production"), required=True)
    q.add_argument("--sha", required=True)
    q.add_argument("--controller", required=True)
    q.add_argument("--pr", default="")
    q.add_argument("--github-output", type=Path, required=True)
    q = sub.add_parser("build")
    q.add_argument("--mode", choices=("isolated-qa", "production"), required=True)
    q.add_argument("--source", type=Path, required=True)
    q.add_argument("--release", required=True)
    q.add_argument("--output", type=Path, required=True)
    q.add_argument("--provider", action="store_true")
    a = p.parse_args()
    try:
        if a.command == "source":
            source = resolve(a.mode, a.sha, a.controller, a.pr)
            with a.github_output.open("a") as f:
                f.write("sha=" + source + "\n")
        else:
            if a.provider:
                verify_provider_source(a.source)
            import boto3
            build(boto3.client("ecr", region_name="ap-northeast-2"), a.source, a.mode, a.release, a.output)
    except Exception:
        p.exit(2, "CANDIDATE_PREPARE_BLOCKED: source, image policy or build verification failed\n")


if __name__ == "__main__":
    main()
