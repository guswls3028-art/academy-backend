"""Trusted controller and artifact checks for PR image builds without PR credentials."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY = "guswls3028-art/academy-backend"
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
TAG = re.compile(r"sha-([0-9a-f]{40})-run-[0-9]+-[0-9]+\Z")
SERVICES = {
    "base": "academy-base",
    "api": "academy-api",
    "ai": "academy-ai-worker-cpu",
    "tools": "academy-tools-worker",
    "messaging": "academy-messaging-worker",
}
DOCKERFILES = {
    "api": "docker/api/Dockerfile",
    "ai": "docker/ai-worker-cpu/Dockerfile",
    "tools": "docker/tools-worker/Dockerfile",
    "messaging": "docker/messaging-worker/Dockerfile",
}
BASE_INPUTS = (
    ".dockerignore", "docker/Dockerfile.base", "docker/native-security",
    "requirements/constraints.txt", "requirements/common.txt",
)
MESSAGING_INPUTS = (
    ".dockerignore", "docker/messaging-worker/Dockerfile",
    "requirements/constraints.txt", "requirements/common.txt",
    "requirements/worker-messaging.txt", "academy", "apps", "libs", "manage.py",
)
MAX_ARCHIVE_BYTES = 12 * 1024**3
MAX_MEMBERS = 2000


class CandidateError(RuntimeError):
    pass


def require_sha(value: str) -> str:
    if not SHA.fullmatch(value):
        raise CandidateError("invalid exact commit SHA")
    return value


def pr_arg(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]{0,5}", value):
        raise argparse.ArgumentTypeError("PR number must be canonical decimal digits")
    return int(value)


def api_json(path: str) -> dict:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise CandidateError("GITHUB_TOKEN is required for source verification")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def verify_source(pr_number: int, source_sha: str, main_sha: str,
                  expected_run_id: int | None = None,
                  expected_run_attempt: int | None = None) -> dict:
    require_sha(source_sha)
    require_sha(main_sha)
    if not 0 < pr_number < 1_000_000:
        raise CandidateError("invalid PR number")
    main = api_json("git/ref/heads/main")
    if main.get("object", {}).get("sha") != main_sha:
        raise CandidateError("main moved since dispatch; start a new run")
    pr = api_json(f"pulls/{pr_number}")
    if not (
        pr.get("state") == "open"
        and pr.get("base", {}).get("ref") == "main"
        and pr.get("base", {}).get("sha") == main_sha
        and pr.get("base", {}).get("repo", {}).get("full_name") == REPOSITORY
        and pr.get("head", {}).get("repo", {}).get("full_name") == REPOSITORY
        and pr.get("head", {}).get("sha") == source_sha
    ):
        raise CandidateError("PR is not an open same-repository PR at the exact head")
    runs = api_json(
        f"actions/workflows/quality-gate.yml/runs?head_sha={source_sha}"
        "&event=pull_request&per_page=100"
    ).get("workflow_runs", [])
    matching = [run for run in runs if run.get("head_sha") == source_sha
                and any(item.get("number") == pr_number for item in run.get("pull_requests", []))]
    if not matching:
        raise CandidateError("no exact PR quality run")
    latest = max(matching, key=lambda run: (run["id"], run.get("run_attempt", 1)))
    if not (latest.get("conclusion") == "success"
            and latest.get("status") == "completed"
            and latest.get("path") == ".github/workflows/quality-gate.yml"
            and latest.get("event") == "pull_request"
            and latest.get("repository", {}).get("full_name") == REPOSITORY
            and latest.get("head_repository", {}).get("full_name") == REPOSITORY
            and any(item.get("number") == pr_number
                    and item.get("head", {}).get("sha") == source_sha
                    and item.get("base", {}).get("sha") == main_sha
                    for item in latest.get("pull_requests", []))):
        raise CandidateError("latest exact PR head Backend Quality Gate is not successful")
    if (expected_run_id is not None and latest["id"] != expected_run_id) or (
            expected_run_attempt is not None and latest.get("run_attempt", 1) != expected_run_attempt):
        raise CandidateError("quality run changed after validation; start a new run")
    jobs = api_json(f"actions/runs/{latest['id']}/jobs?per_page=100").get("jobs", [])
    actual = {job.get("name"): job.get("conclusion") for job in jobs}
    required = (
        "Detect native security image changes",
        "Backend static and migration contract",
        "Backend Django smoke and deployment contracts",
        "PostgreSQL transaction and tenant contracts",
    )
    if any(actual.get(name) != "success" for name in required) or actual.get(
        "Native security arm64 image contract"
    ) not in ("success", "skipped"):
        raise CandidateError("required quality jobs did not all pass")
    return latest


def git(repo_dir: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo_dir), *args],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise CandidateError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def same_inputs(repo_dir: Path, previous_sha: str, source_sha: str, paths: tuple[str, ...]) -> bool:
    for commit in (previous_sha, source_sha):
        git(repo_dir, "cat-file", "-e", f"{require_sha(commit)}^{{commit}}")
    result = subprocess.run(["git", "-C", str(repo_dir), "diff", "--quiet",
                             previous_sha, source_sha, "--", *paths], check=False)
    if result.returncode not in (0, 1):
        raise CandidateError("cannot compare immutable image build inputs")
    return result.returncode == 0


def manifest_image(manifest: dict, repository: str) -> tuple[str, str, str]:
    images = manifest.get("images")
    if not isinstance(images, dict) or not isinstance(images.get(repository), dict):
        raise CandidateError(f"verified baseline {repository} entry is malformed")
    image = images[repository]
    digest, tag = image.get("digest"), image.get("tag")
    match = TAG.fullmatch(tag or "")
    if not DIGEST.fullmatch(digest or ""):
        raise CandidateError(f"verified baseline {repository} has no immutable digest")
    return digest, match.group(1) if match else "", tag if match else ""


def plan(args: argparse.Namespace) -> None:
    quality_run = verify_source(args.pr, args.source_sha, args.main_sha)
    if git(args.repo_dir, "rev-parse", "HEAD") != args.main_sha:
        raise CandidateError("controller code is not the dispatched main commit")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not (manifest.get("schemaVersion") == 1 and manifest.get("status") == "successful"
            and manifest.get("complete") is True and SHA.fullmatch(manifest.get("gitSha", ""))):
        raise CandidateError("no complete successful baseline manifest")
    base_digest, base_source, base_tag = manifest_image(manifest, SERVICES["base"])
    msg_digest, msg_source, msg_tag = manifest_image(manifest, SERVICES["messaging"])
    reuse_base = bool(base_source) and same_inputs(
        args.repo_dir, base_source, args.source_sha, BASE_INPUTS
    )
    if not same_inputs(args.repo_dir, args.main_sha, args.source_sha,
                       (".github/workflows/quality-gate.yml",)):
        raise CandidateError("PR changes the trusted quality-gate workflow")
    reuse_messaging = reuse_base and bool(msg_source) and same_inputs(
        args.repo_dir, msg_source, args.source_sha, MESSAGING_INPUTS
    )
    build_services = ["api", "ai", "tools"] + ([] if reuse_messaging else ["messaging"])
    publish_services = ([] if reuse_base else ["base"]) + build_services
    outputs = {
        "source_sha": args.source_sha,
        "reuse_base": str(reuse_base).lower(),
        "reuse_messaging": str(reuse_messaging).lower(),
        "base_digest": base_digest,
        "base_tag": base_tag if reuse_base else "",
        "base_source_sha": base_source if reuse_base else args.source_sha,
        "messaging_digest": msg_digest if reuse_messaging else "",
        "messaging_tag": msg_tag if reuse_messaging else "",
        "quality_run_id": str(quality_run["id"]),
        "quality_run_attempt": str(quality_run.get("run_attempt", 1)),
        "build_matrix": json.dumps({"include": [{"service": service, "repository": SERVICES[service],
                                                  "dockerfile": DOCKERFILES[service]}
                                                 for service in build_services]}, separators=(",", ":")),
        "publish_matrix": json.dumps({"include": [{"service": service, "repository": SERVICES[service]}
                                                   for service in publish_services]}, separators=(",", ":")),
    }
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
        for key, value in outputs.items():
            stream.write(f"{key}={value}\n")
    print(f"source={args.source_sha} reuse_base={reuse_base} reuse_messaging={reuse_messaging}")


def image_id(image: str) -> tuple[str, str, str]:
    info = json.loads(subprocess.check_output(["docker", "image", "inspect", image], text=True))[0]
    result = (info["Id"], info["Os"], info["Architecture"])
    if not DIGEST.fullmatch(result[0]) or result[1:] != ("linux", "arm64"):
        raise CandidateError("image is not a single linux/arm64 image")
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def receipt(args: argparse.Namespace) -> None:
    image = f"{SERVICES[args.service]}:candidate"
    ident, system, architecture = image_id(image)
    data = {"schemaVersion": 1, "service": args.service, "sourceSha": require_sha(args.source_sha),
            "runId": args.run_id, "runAttempt": args.run_attempt,
            "archiveSha256": sha256_file(args.archive), "archiveBytes": args.archive.stat().st_size,
            "imageId": ident, "os": system, "architecture": architecture,
            "imageTag": image}
    if data["archiveBytes"] > MAX_ARCHIVE_BYTES:
        raise CandidateError("image archive exceeds bounded runner capacity")
    args.output.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")


def inspect_archive(archive: Path, expected_tag: str) -> str:
    manifest_data = None
    configs: dict[str, bytes] = {}
    total_size = 0
    count = 0
    with gzip.open(archive, "rb") as decompressed:
        with tarfile.open(fileobj=decompressed, mode="r|") as tar:
            for member in tar:
                count += 1
                total_size += member.size
                if count > MAX_MEMBERS or total_size > MAX_ARCHIVE_BYTES:
                    raise CandidateError("Docker archive exceeds bounded size/member count")
                name = member.name.rstrip("/") if member.isdir() else member.name
                if (name.startswith("/") or "\\" in name or
                        any(part in ("", ".", "..") for part in name.split("/")) or
                        not (member.isfile() or member.isdir())):
                    raise CandidateError("Docker archive contains unsafe member")
                if name == "manifest.json":
                    if member.size > 1024 * 1024 or manifest_data is not None:
                        raise CandidateError("invalid Docker archive manifest")
                    manifest_data = tar.extractfile(member).read()
                elif (name.endswith(".json") or name.startswith("blobs/sha256/")) and member.size <= 1024 * 1024:
                    configs[name] = tar.extractfile(member).read()
    if manifest_data is None:
        raise CandidateError("Docker archive manifest missing")
    manifest = json.loads(manifest_data)
    if len(manifest) != 1 or manifest[0].get("RepoTags") != [expected_tag]:
        raise CandidateError("Docker archive has unexpected image/tag set")
    config_name = manifest[0].get("Config")
    if config_name not in configs:
        raise CandidateError("Docker archive config missing")
    config = configs[config_name]
    obj = json.loads(config)
    if obj.get("os") != "linux" or obj.get("architecture") != "arm64":
        raise CandidateError("Docker archive config is not linux/arm64")
    return "sha256:" + hashlib.sha256(config).hexdigest()


def verify_archive(args: argparse.Namespace) -> None:
    data = json.loads(args.receipt.read_text(encoding="utf-8"))
    tag = f"{SERVICES[args.service]}:candidate"
    if not (data.get("schemaVersion") == 1 and data.get("service") == args.service
            and data.get("sourceSha") == require_sha(args.source_sha)
            and data.get("runId") == args.run_id and data.get("runAttempt") == args.run_attempt
            and data.get("imageTag") == tag and data.get("os") == "linux"
            and data.get("architecture") == "arm64"
            and data.get("archiveBytes") == args.archive.stat().st_size
            and 0 < data["archiveBytes"] <= MAX_ARCHIVE_BYTES
            and data.get("archiveSha256") == sha256_file(args.archive)):
        raise CandidateError("artifact receipt identity, size or SHA-256 mismatch")
    if inspect_archive(args.archive, tag) != data.get("imageId"):
        raise CandidateError("Docker archive config differs from receipt")
    if args.loaded and image_id(tag)[0] != data["imageId"]:
        raise CandidateError("loaded image differs from verified artifact")
    print(f"verified {args.service} {data['imageId']} archive={data['archiveSha256']}")


def scan(args: argparse.Namespace) -> None:
    gate_path = args.repo_dir / "scripts/v1/ecr-critical-scan-gate.py"
    spec = importlib.util.spec_from_file_location("ecr_critical_scan_gate", gate_path)
    if not spec or not spec.loader:
        raise CandidateError("cannot load existing ECR scan policy")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    today = datetime.now(timezone.utc).date()
    acceptances = gate.load_acceptances(args.repo_dir / "docs/ssot/ecr-critical-risk-acceptance.json", today)
    baselines, known = gate.load_high_baselines(args.repo_dir / "docs/ssot/ecr-high-risk-baseline.json", today)
    for service in args.services:
        repository = SERVICES[service]
        digest = args.digests[service]
        if not DIGEST.fullmatch(digest):
            raise CandidateError(f"invalid {service} ECR digest")
        findings = gate.wait_for_completed_scan(repository, digest, args.region, 40, 15)
        gate.evaluate_findings(repository, findings, acceptances)
        gate.evaluate_high_budget(repository, findings, baselines, known)
        print(f"ECR_SCAN_PASS repo={repository} digest={digest}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--pr", type=pr_arg, required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--main-sha", required=True)
    p.add_argument("--repo-dir", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p = sub.add_parser("verify-source")
    p.add_argument("--pr", type=pr_arg, required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--main-sha", required=True)
    p.add_argument("--expected-quality-run-id", type=int)
    p.add_argument("--expected-quality-run-attempt", type=int)
    p = sub.add_parser("receipt")
    p.add_argument("--service", choices=SERVICES, required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--run-attempt", required=True)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("verify-archive")
    p.add_argument("--service", choices=SERVICES, required=True)
    p.add_argument("--source-sha", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--run-attempt", required=True)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--receipt", type=Path, required=True)
    p.add_argument("--loaded", action="store_true")
    p = sub.add_parser("scan")
    p.add_argument("--repo-dir", type=Path, required=True)
    p.add_argument("--region", required=True)
    p.add_argument("--services", choices=SERVICES, nargs="+", required=True)
    p.add_argument("--digests", type=json.loads, required=True)
    args = parser.parse_args()
    try:
        {"plan": plan, "verify-source": lambda a: verify_source(
            a.pr, a.source_sha, a.main_sha, a.expected_quality_run_id,
            a.expected_quality_run_attempt),
         "receipt": receipt, "verify-archive": verify_archive,
         "scan": scan}[args.command](args)
    except (CandidateError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
