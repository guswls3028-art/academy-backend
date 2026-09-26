"""Focused trust-boundary contracts for the build-only PR path."""

import gzip
import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.v1 import candidate_build_only as candidate


MAIN = "a" * 40
SOURCE = "b" * 40


def api_fixture(base=MAIN, conclusion="success", jobs=None):
    pr = {"state": "open", "base": {"ref": "main", "sha": MAIN,
                                    "repo": {"full_name": candidate.REPOSITORY}},
          "head": {"sha": SOURCE, "repo": {"full_name": candidate.REPOSITORY}}}
    run = {"id": 42, "run_attempt": 1, "conclusion": conclusion, "status": "completed",
           "path": ".github/workflows/quality-gate.yml", "event": "pull_request",
           "head_sha": SOURCE, "repository": {"full_name": candidate.REPOSITORY},
           "head_repository": {"full_name": candidate.REPOSITORY},
           "pull_requests": [{"number": 509, "head": {"sha": SOURCE}, "base": {"sha": base}}]}
    job_names = (
        "Detect native security image changes",
        "Backend static and migration contract",
        "Backend Django smoke and deployment contracts",
        "PostgreSQL transaction and tenant contracts",
    )
    actual_jobs = jobs or [{"name": name, "conclusion": "success"} for name in job_names]
    actual_jobs.append({"name": "Native security arm64 image contract", "conclusion": "skipped"})

    def fake_api(path):
        if path == "git/ref/heads/main":
            return {"object": {"sha": MAIN}}
        if path == "pulls/509":
            return pr
        if path.startswith("actions/workflows/"):
            return {"workflow_runs": [run]}
        if path == "actions/runs/42/jobs?per_page=100":
            return {"jobs": actual_jobs}
        raise AssertionError(path)

    return fake_api


class SourceTests(unittest.TestCase):
    def test_pr_input_rejects_leading_zero(self):
        with self.assertRaisesRegex(Exception, "canonical"):
            candidate.pr_arg("0509")

    def test_exact_current_base_and_required_jobs_pass(self):
        with patch.object(candidate, "api_json", side_effect=api_fixture()):
            self.assertEqual(candidate.verify_source(509, SOURCE, MAIN)["id"], 42)

    def test_old_base_or_rerun_fails_closed(self):
        with patch.object(candidate, "api_json", side_effect=api_fixture(base="c" * 40)):
            with self.assertRaisesRegex(candidate.CandidateError, "Quality Gate"):
                candidate.verify_source(509, SOURCE, MAIN)
        with patch.object(candidate, "api_json", side_effect=api_fixture()):
            with self.assertRaisesRegex(candidate.CandidateError, "changed"):
                candidate.verify_source(509, SOURCE, MAIN, expected_run_id=41)

    def test_missing_django_job_fails(self):
        jobs = [{"name": "Detect native security image changes", "conclusion": "success"}]
        with patch.object(candidate, "api_json", side_effect=api_fixture(jobs=jobs)):
            with self.assertRaisesRegex(candidate.CandidateError, "required quality"):
                candidate.verify_source(509, SOURCE, MAIN)

    def test_newer_failed_run_supersedes_success(self):
        fixture = api_fixture()

        def newer_failure(path):
            result = fixture(path)
            if path.startswith("actions/workflows/"):
                old = result["workflow_runs"][0]
                return {"workflow_runs": [old, {**old, "id": 43, "conclusion": "failure"}]}
            return result

        with patch.object(candidate, "api_json", side_effect=newer_failure):
            with self.assertRaisesRegex(candidate.CandidateError, "Quality Gate"):
                candidate.verify_source(509, SOURCE, MAIN)

    def test_plan_reuses_only_equal_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {"schemaVersion": 1, "status": "successful", "complete": True,
                        "gitSha": MAIN,
                        "images": {repo: {"digest": "sha256:" + "d" * 64,
                                          "tag": f"sha-{MAIN}-run-1-1"}
                                   for repo in ("academy-base", "academy-messaging-worker")}}
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest))
            output = root / "output"
            args = SimpleNamespace(pr=509, source_sha=SOURCE, main_sha=MAIN,
                                   repo_dir=root, manifest=path)
            with (patch.object(candidate, "verify_source", return_value={"id": 42, "run_attempt": 1}),
                  patch.object(candidate, "git", return_value=MAIN),
                  patch.object(candidate, "same_inputs", side_effect=[True, True, False]),
                  patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)})):
                candidate.plan(args)
            values = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertEqual(values["reuse_base"], "true")
            self.assertEqual(values["reuse_messaging"], "false")
            self.assertEqual([x["service"] for x in json.loads(values["publish_matrix"])["include"]],
                             ["api", "ai", "tools", "messaging"])

    def test_changed_base_requires_new_base_and_messaging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {"schemaVersion": 1, "status": "successful", "complete": True,
                        "gitSha": MAIN,
                        "images": {repo: {"digest": "sha256:" + "d" * 64,
                                          "tag": f"sha-{MAIN}-run-1-1"}
                                   for repo in ("academy-base", "academy-messaging-worker")}}
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest))
            output = root / "output"
            args = SimpleNamespace(pr=509, source_sha=SOURCE, main_sha=MAIN,
                                   repo_dir=root, manifest=path)
            with (patch.object(candidate, "verify_source", return_value={"id": 42}),
                  patch.object(candidate, "git", return_value=MAIN),
                  patch.object(candidate, "same_inputs", side_effect=[False, True]),
                  patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)})):
                candidate.plan(args)
            values = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertEqual(values["reuse_base"], "false")
            self.assertEqual(values["reuse_messaging"], "false")
            self.assertEqual([x["service"] for x in json.loads(values["publish_matrix"])["include"]],
                             ["base", "api", "ai", "tools", "messaging"])


class ArchiveTests(unittest.TestCase):
    def make_archive(self, path, tag="academy-api:candidate", architecture="arm64",
                     extra_name=None):
        config = json.dumps({"os": "linux", "architecture": architecture}).encode()
        config_name = hashlib.sha256(config).hexdigest() + ".json"
        manifest = json.dumps([{"Config": config_name, "RepoTags": [tag], "Layers": []}]).encode()
        members = [(config_name, config), ("manifest.json", manifest)]
        if extra_name:
            members.append((extra_name, b"bad"))
        with gzip.open(path, "wb") as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for name, body in members:
                    member = tarfile.TarInfo(name)
                    member.size = len(body)
                    archive.addfile(member, io.BytesIO(body))
        return "sha256:" + hashlib.sha256(config).hexdigest()

    def test_archive_accepts_exact_single_arm64_image(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.tar.gz"
            expected = self.make_archive(path)
            self.assertEqual(candidate.inspect_archive(path, "academy-api:candidate"), expected)

    def test_archive_rejects_wrong_tag_arch_and_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.tar.gz"
            self.make_archive(path)
            with self.assertRaisesRegex(candidate.CandidateError, "unexpected image"):
                candidate.inspect_archive(path, "academy-tools-worker:candidate")
            self.make_archive(path, architecture="amd64")
            with self.assertRaisesRegex(candidate.CandidateError, "linux/arm64"):
                candidate.inspect_archive(path, "academy-api:candidate")
            self.make_archive(path, extra_name="../escape")
            with self.assertRaisesRegex(candidate.CandidateError, "unsafe member"):
                candidate.inspect_archive(path, "academy-api:candidate")

    def test_receipt_sha_mismatch_fails_before_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.tar.gz"
            image_id = self.make_archive(path)
            receipt = Path(directory) / "receipt.json"
            receipt.write_text(json.dumps({"schemaVersion": 1, "service": "api",
                                           "sourceSha": SOURCE, "runId": "10",
                                           "runAttempt": "1", "imageTag": "academy-api:candidate",
                                           "os": "linux", "architecture": "arm64",
                                           "imageId": image_id, "archiveBytes": path.stat().st_size,
                                           "archiveSha256": "0" * 64}))
            args = SimpleNamespace(service="api", source_sha=SOURCE, run_id="10",
                                   run_attempt="1", archive=path, receipt=receipt, loaded=False)
            with self.assertRaisesRegex(candidate.CandidateError, "receipt"):
                candidate.verify_archive(args)


if __name__ == "__main__":
    unittest.main()
