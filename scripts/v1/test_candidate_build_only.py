"""Focused trust-boundary contracts for the build-only PR path."""

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

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
    def test_capacity_preflight_requires_image_size_plus_reserved_space(self):
        args = SimpleNamespace(image="academy-api:candidate", directory=Path("."))
        with (patch.object(candidate.subprocess, "check_output", return_value='[{"Size":4294967296}]'),
              patch.object(candidate.shutil, "disk_usage", return_value=SimpleNamespace(free=5 * 1024**3))):
            with self.assertRaisesRegex(candidate.CandidateError, "insufficient runner space"):
                candidate.check_capacity(args)

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


class ProductionChangeDetectionTests(unittest.TestCase):
    def test_candidate_controller_only_is_excluded_but_runtime_changes_still_build(self):
        workflow = (Path(__file__).resolve().parents[2] /
                    ".github/workflows/v1-build-and-push-latest.yml").read_text(encoding="utf-8")
        for exact_path in (
            ".github/workflows/candidate-build-only.yml",
            "scripts/v1/candidate_build_only.py",
            "scripts/v1/test_candidate_build_only.py",
        ):
            self.assertIn(f"      - '{exact_path}'", workflow)
        self.assertNotIn("      - 'scripts/v1/**'", workflow)
        function = re.search(r"^\s*runtime_changes\(\) \{[^\n]+\}", workflow, re.MULTILINE)
        self.assertIsNotNone(function)

        def filtered(paths):
            result = subprocess.run(
                ["bash", "-c", function.group(0) + '\nruntime_changes "$CHANGED"'],
                env={**os.environ, "CHANGED": "\n".join(paths)},
                capture_output=True, text=True, check=True,
            )
            return result.stdout.strip().splitlines() if result.stdout.strip() else []

        candidate_paths = [
            ".github/workflows/candidate-build-only.yml",
            "scripts/v1/candidate_build_only.py",
            "scripts/v1/test_candidate_build_only.py",
        ]
        self.assertEqual(filtered(candidate_paths), [])
        self.assertEqual(filtered(candidate_paths + ["docs/README.md"]), ["docs/README.md"])
        self.assertEqual(
            filtered(candidate_paths + ["apps/domains/exams/views/exam_view.py",
                                        "scripts/v1/deploy.ps1",
                                        "scripts/v1/candidate_build_only_extra.py"]),
            ["apps/domains/exams/views/exam_view.py", "scripts/v1/deploy.ps1",
             "scripts/v1/candidate_build_only_extra.py"],
        )


class WorkflowCompletionTests(unittest.TestCase):
    def test_completion_shell_rejects_every_non_success_result(self):
        shell = ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash"))
        if not shell or not Path(shell).is_file():
            self.skipTest("Bash is required to execute the exact workflow completion step")
        workflow = yaml.safe_load((Path(__file__).resolve().parents[2] /
                                  ".github/workflows/candidate-build-only.yml").read_text(encoding="utf-8"))
        step = workflow["jobs"]["completion"]["steps"][0]
        successful = dict.fromkeys(step["env"], "success")
        cases = [successful]
        for variable in successful:
            for result in ("skipped", "failure", "cancelled", ""):
                cases.append({**successful, variable: result})
        for values in cases:
            with self.subTest(values=values):
                result = subprocess.run([shell, "-c", step["run"]], env={**os.environ, **values},
                                        capture_output=True, timeout=10, check=False)
                self.assertEqual(result.returncode == 0, values == successful)

    def test_optional_base_skip_cannot_suppress_publication_or_hide_missing_receipt(self):
        workflow = yaml.safe_load((Path(__file__).resolve().parents[2] /
                                  ".github/workflows/candidate-build-only.yml").read_text(encoding="utf-8"))
        jobs = workflow["jobs"]
        # A status function is required even when the direct dependency passed:
        # the intentionally skipped base ancestor otherwise injects success().
        for job_id, upstream in (("publish", "build-runtime"), ("finalize", "publish")):
            condition = jobs[job_id]["if"]
            self.assertIn("!cancelled()", condition)
            self.assertIn("needs.validate.result == 'success'", condition)
            self.assertIn(f"needs.{upstream}.result == 'success'", condition)
        completion = jobs["completion"]
        self.assertIn("always()", completion["if"])
        self.assertEqual(set(completion["needs"]), {"validate", "publish", "finalize"})
        self.assertEqual(completion["permissions"], {})
        self.assertNotIn("environment", completion)
        step = completion["steps"][0]
        for variable, upstream in (("VALIDATION_RESULT", "validate"),
                                   ("PUBLICATION_RESULT", "publish"),
                                   ("FINALIZATION_RESULT", "finalize")):
            self.assertEqual(step["env"][variable], "${{ needs." + upstream + ".result }}")
            self.assertIn(f'test "${variable}" = success', step["run"])


class CompleteReceiptTests(unittest.TestCase):
    def test_seal_requires_every_published_digest_to_match_final_ecr_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            digest = "sha256:" + "d" * 64
            qa_tag = f"qa-pr-509-{SOURCE}-run-10-1"
            old_tag = f"sha-{MAIN}-run-1-1"
            images = {
                service: {"repository": repo, "digest": digest,
                          "tag": old_tag if service in ("base", "messaging") else qa_tag}
                for service, repo in candidate.SERVICES.items()
            }
            image_path = root / "images.json"
            image_path.write_text(json.dumps(images))
            for service in ("api", "ai", "tools"):
                (root / f"published-{service}.json").write_text(json.dumps({
                    "sourceSha": SOURCE, "repository": candidate.SERVICES[service],
                    "digest": digest, "tag": qa_tag, "runId": 10, "runAttempt": 1,
                    "artifact": {"schemaVersion": 1, "service": service,
                                 "sourceSha": SOURCE, "runId": "10", "runAttempt": "1",
                                 "imageTag": candidate.SERVICES[service] + ":candidate",
                                 "imageId": digest, "archiveSha256": "e" * 64},
                }))
            args = SimpleNamespace(images=image_path, published_dir=root, source_sha=SOURCE,
                                   main_sha=MAIN, pr=509, run_id="10", run_attempt="1",
                                   quality_run_id="42", quality_run_attempt="1",
                                   qa_tag=qa_tag, reuse_base="true", reuse_messaging="true",
                                   base_digest=digest, messaging_digest=digest,
                                   output=root / "complete.json")
            candidate.seal(args)
            result = json.loads(args.output.read_text())
            self.assertTrue(result["complete"])
            self.assertEqual(result["images"]["api"]["artifactSha256"], "e" * 64)
            self.assertTrue(result["images"]["base"]["reused"])
            images["api"]["digest"] = "sha256:" + "f" * 64
            image_path.write_text(json.dumps(images))
            with self.assertRaisesRegex(candidate.CandidateError, "publication receipt"):
                candidate.seal(args)


if __name__ == "__main__":
    unittest.main()
