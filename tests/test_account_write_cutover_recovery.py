"""Offline manual recovery checks; no cloud calls or production evidence."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.v1 import account_write_cutover_recovery as r
from tests import test_account_write_cutover as fixture


BINDING = {**fixture.BINDING, "owner": "ci-deploy:123:1", "runAttempt": "1"}


class OwnedLock(fixture.Lock):
    def load_window(self, table, owner):
        r.core.require(self.live and table == r.core.TABLE and owner == BINDING["owner"], "lock-owner")
        return copy.deepcopy(self.state)


class RecoveryAws(fixture.FakeAws):
    def __init__(self, plan, lock):
        super().__init__(plan, lock)
        self.owner = BINDING["owner"]
        self.proof_value = None
        self.deleted = False
        self.identity = {"Account": r.core.ACCOUNT, "Arn": f"arn:aws:iam::{r.core.ACCOUNT}:root"}
        self.writes = []
        self.bad_pin = False
        self.before_update = None
        self.delete_fails = False

    def item(self):
        if self.deleted:
            return {}
        value = {"owner": {"S": self.owner}, "ttl": {"N": "10000"}}
        if self.lock.state is not None:
            value.update(ttl={"N": str(r.deployment_lock.WINDOW_PIN_TTL)},
                         accountWindowTtl={"N": "1" if self.bad_pin else str(r.deployment_lock.WINDOW_PIN_TTL)},
                         accountWindow={"S": r.core.canonical(self.lock.state)})
        if self.proof_value is not None:
            value[r.MARKER] = {"S": self.proof_value}
        return value

    def __call__(self, service, operation, **args):
        if service == "sts":
            return copy.deepcopy(self.identity)
        if service != "dynamodb":
            return super().__call__(service, operation, **args)
        if operation == "get-item":
            assert args["consistent_read"] is True
            return {"Item": self.item()}
        values = args["expression_attribute_values"]
        if operation == "update-item":
            if self.before_update:
                self.before_update()
            current = self.item()
            r.require(current.get("owner") == values[":owner"]
                      and current.get("ttl") == values[":pin"]
                      and current.get("accountWindowTtl") == values[":pin"]
                      and current.get("accountWindow") == values[":window"]
                      and current.get(r.MARKER, values[":proof"]) == values[":proof"], "recovery-cas-failed")
            self.proof_value = values[":proof"]["S"]
        elif operation == "delete-item":
            r.require(not self.delete_fails, "recovery-aws-unconfirmed")
            current = self.item()
            r.require(current.get("owner") == values[":owner"]
                      and "accountWindow" not in current and "accountWindowTtl" not in current
                      and current.get(r.MARKER) == values[":proof"]
                      and int(current["ttl"]["N"]) > int(values[":now"]["N"]), "recovery-cas-failed")
            self.deleted = True
        else:
            raise AssertionError(operation)
        self.writes.append((operation, copy.deepcopy(args)))
        return {}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.plan = fixture.plan()
        self.plan["binding"] = BINDING.copy()
        self.plan["id"] = r.core.digest({key: value for key, value in self.plan.items() if key != "id"})
        self.lock = OwnedLock()
        self.aws = RecoveryAws(self.plan, self.lock)
        self.run_info = {"id": 123, "run_attempt": 1, "head_sha": BINDING["source"],
                         "repository": {"full_name": r.REPOSITORY},
                         "head_repository": {"full_name": r.REPOSITORY},
                         "path": r.WORKFLOW, "event": "push", "head_branch": "main",
                         "status": "completed", "conclusion": "failure"}
        self.approvals = [{"state": "approved", "user": {"id": 42},
                           "environments": [{"name": "production", "id": 17}]}]
        self.freshness = Mock()
        self.gh_calls = []
        self.runner = r.Recovery(self.aws, gh=self.gh, freshness=self.freshness,
                                 lock=self.lock, observer=self.aws.observe, clock=lambda: 100)
        window = r.core.Window(self.aws, self.lock, r.core.TABLE, self.plan, BINDING,
                               self.aws.observe, clock=lambda: 100, wait=lambda _: None)
        window.open()
        self.aws.events.clear()

    def gh(self, path):
        self.gh_calls.append(path)
        return copy.deepcopy(self.approvals if path.endswith("/approvals") else self.run_info)

    def recover(self, apply=False, **kwargs):
        return self.runner.run(self.plan, kwargs.get("owner", BINDING["owner"]),
                               kwargs.get("source", BINDING["source"]), apply=apply)

    def assert_paused(self):
        self.assertIsNotNone(self.lock.state)
        window = r.core.Window(self.aws, self.lock, r.core.TABLE, self.plan, BINDING, self.aws.observe)
        window.load()
        window.verify_pause()

    def test_plan_verifies_actual_runtime_without_resource_writes(self):
        before = copy.deepcopy(self.lock.state)
        result = self.recover()
        self.assertFalse(result["released"])
        self.assertTrue(result["runtimeVerified"])
        self.assertEqual(result["restorationRuntime"], "previous")
        self.assertEqual(before, self.lock.state)
        self.assertEqual(self.aws.writes, [])
        self.assertEqual(self.aws.events, [])
        self.assertIsNone(self.aws.proof_value)
        self.freshness.assert_called_once_with(BINDING["source"])

    def test_apply_restores_previous_and_cas_releases_only_own_lock(self):
        result = self.recover(apply=True)
        self.assertEqual(result["restorationRuntime"], "previous")
        self.assertTrue(result["released"] and result["runtimeVerified"])
        self.assertTrue(self.aws.deleted)
        self.assertIsNone(self.lock.state)
        self.assertEqual([op for op, _ in self.aws.writes], ["update-item", "delete-item"])
        delete = self.aws.writes[-1][1]
        self.assertIn("attribute_not_exists(#window)", delete["condition_expression"])
        self.assertIn("attribute_not_exists(#pin)", delete["condition_expression"])
        self.assertIn("#proof = :proof", delete["condition_expression"])
        self.assertEqual(self.freshness.call_count, 2)

    def test_apply_complete_candidate(self):
        self.aws.promote()
        result = self.recover(apply=True)
        self.assertEqual(result["restorationRuntime"], "candidate")
        self.assertTrue(result["released"])

    def test_release_failure_resumes_with_exact_original_extended_fleet_evidence(self):
        extra = "i-000000000000000ff"
        self.lock.state["oldFleet"]["academy-tools-worker"].append(extra)
        self.aws.delete_fails = True
        with self.assertRaises(r.RecoveryError):
            self.recover(apply=True)
        self.assertIsNone(self.lock.state)
        self.assertFalse(self.aws.deleted)
        original = json.loads(self.aws.proof_value)
        self.assertIn(extra, original["oldFleet"]["academy-tools-worker"])
        self.aws.delete_fails = False
        self.aws.promote()
        # The plan did not know this extra consumer. The original actual proof
        # must still reject a candidate reopen until that consumer is retired.
        with self.assertRaises(r.core.CutoverError):
            self.recover(apply=True)
        self.assertFalse(self.aws.deleted)
        self.aws.terminated.add(extra)
        self.assertTrue(self.recover(apply=True)["released"])

    def test_cleared_window_without_original_proof_cannot_release(self):
        self.lock.state = None
        with self.assertRaisesRegex(r.RecoveryError, "cleared-proof"):
            self.recover(apply=True)
        self.assertEqual(self.aws.writes, [])

    def test_wrong_run_source_attempt_repo_workflow_or_branch_rejected(self):
        for field, value in (("id", 124), ("run_attempt", 2), ("head_sha", "b" * 40),
                             ("repository", {"full_name": "foreign/repo"}),
                             ("head_repository", {"full_name": "foreign/repo"}),
                             ("path", ".github/workflows/other.yml"), ("head_branch", "other")):
            with self.subTest(field=field):
                saved = self.run_info.copy()
                self.run_info[field] = value
                with self.assertRaises(r.RecoveryError):
                    self.recover(apply=True)
                self.run_info = saved
        self.assertEqual(self.aws.writes, [])

    def test_active_or_nonfailed_run_rejected(self):
        for status, conclusion in (("in_progress", None), ("queued", None), ("completed", "success")):
            self.run_info.update(status=status, conclusion=conclusion)
            with self.subTest(status=status), self.assertRaises(r.RecoveryError):
                self.recover(apply=True)
        self.assertEqual(self.aws.writes, [])

    def test_cancelled_original_run_eligible(self):
        self.run_info["conclusion"] = "cancelled"
        self.assertFalse(self.recover()["released"])

    def test_unapproved_or_other_environment_rejected(self):
        for reviews in ([], [{"state": "rejected", "environments": [{"name": "production", "id": 17}]}],
                        [{"state": "approved", "user": {"id": 42}, "environments": [{"name": "staging", "id": 17}]}]):
            self.approvals = reviews
            with self.subTest(), self.assertRaises(r.RecoveryError):
                self.recover(apply=True)
        self.assertEqual(self.aws.writes, [])

    def test_approval_from_an_earlier_attempt_is_not_reused(self):
        binding = {**BINDING, "owner": "ci-deploy:123:2", "runAttempt": "2"}
        plan = copy.deepcopy(self.plan)
        plan["binding"] = binding
        plan["id"] = r.core.digest({key: value for key, value in plan.items() if key != "id"})
        self.run_info["run_attempt"] = 2
        with self.assertRaisesRegex(r.RecoveryError, "approval-attempt-unprovable"):
            self.runner.run(plan, binding["owner"], binding["source"], apply=True)
        self.assertEqual(self.aws.writes, [])

    def test_manual_validation_never_calls_ci_binding_or_changes_environment(self):
        before = dict(os.environ)
        with patch.object(r.core, "ci_binding", side_effect=AssertionError("CI owner adoption")):
            self.assertTrue(self.recover(apply=True)["released"])
        self.assertEqual(dict(os.environ), before)

    def test_dirty_stale_or_divergent_source_rejected_before_cloud_access(self):
        for code in ("dirty-source", "stale-source", "divergent-source"):
            self.freshness.side_effect = r.RecoveryError(code)
            with self.subTest(code=code), self.assertRaises(r.RecoveryError):
                self.recover(apply=True)
        self.assertEqual(self.gh_calls, [])
        self.assertEqual(self.aws.writes, [])

    def test_wrong_owner_or_source_cannot_adopt_original_binding(self):
        for kwargs in ({"owner": "ci-deploy:123:2"}, {"source": "b" * 40}):
            with self.subTest(), self.assertRaises(r.core.CutoverError):
                self.recover(apply=True, **kwargs)
        self.freshness.assert_not_called()

    def test_wrong_live_ddb_owner_refused(self):
        self.aws.owner = "ci-deploy:124:1"
        with self.assertRaises(r.RecoveryError):
            self.recover(apply=True)
        self.assertEqual(self.aws.writes, [])

    def test_different_window_plan_refused(self):
        self.lock.state["plan"] = {**self.plan, "id": "f" * 64}
        with self.assertRaises(r.core.CutoverError):
            self.recover(apply=True)
        self.assertEqual(self.aws.writes, [])

    def test_corrupt_pin_refused(self):
        self.aws.bad_pin = True
        with self.assertRaises(r.RecoveryError):
            self.recover(apply=True)
        self.assertEqual(self.aws.writes, [])

    def test_foreign_existing_proof_never_overwritten(self):
        self.aws.proof_value = r.core.canonical({"version": 1, "binding": {**BINDING, "owner": "foreign"}})
        with self.assertRaises(r.RecoveryError):
            self.recover(apply=True)
        self.assertEqual(self.aws.writes, [])

    def test_owner_race_fails_marker_cas_without_reopening(self):
        self.aws.before_update = lambda: setattr(self.aws, "owner", "foreign")
        with self.assertRaises(r.RecoveryError):
            self.recover(apply=True)
        self.assertEqual(self.aws.writes, [])
        self.assert_paused()

    def test_mixed_runtime_error_retains_pin_and_pause(self):
        self.aws.promote()
        repo = "academy-tools-worker"
        self.aws.images[self.aws.members[repo][0]] = self.plan["previous"]["images"][repo]["digest"]
        with self.assertRaises(r.core.CutoverError):
            self.recover(apply=True)
        self.assert_paused()
        self.assertIsNotNone(self.aws.proof_value)
        self.assertEqual([op for op, _ in self.aws.writes], ["update-item"])

    def test_future_launch_mismatch_error_retains_pin(self):
        self.aws.latest_versions["academy-api"] = 8
        with self.assertRaises(r.core.CutoverError):
            self.recover(apply=True)
        self.assert_paused()

    def test_plan_mixed_runtime_does_not_mutate_resources(self):
        self.aws.promote()
        repo = "academy-tools-worker"
        self.aws.images[self.aws.members[repo][0]] = self.plan["previous"]["images"][repo]["digest"]
        with self.assertRaises(r.core.CutoverError):
            self.recover()
        self.assertEqual(self.aws.writes, [])
        self.assertEqual(self.aws.events, [])
        self.assertIsNone(self.aws.proof_value)

    def test_lost_core_mutation_ack_uses_existing_exact_readback(self):
        self.aws.lost = "delete-rule"
        self.assertTrue(self.recover(apply=True)["released"])

    def test_only_account_root_or_exact_deployment_role_is_accepted(self):
        self.aws.identity["Arn"] = f"arn:aws:sts::{r.core.ACCOUNT}:assumed-role/academy-gha-ecr-build/manual-session"
        self.assertFalse(self.recover()["released"])
        for identity in ({"Account": "000000000000", "Arn": "arn:aws:iam::000000000000:root"},
                         {"Account": r.core.ACCOUNT, "Arn": f"arn:aws:iam::{r.core.ACCOUNT}:user/admin"},
                         {"Account": r.core.ACCOUNT, "Arn": f"arn:aws:sts::{r.core.ACCOUNT}:assumed-role/unknown/manual"}):
            self.aws.identity = identity
            with self.subTest(), self.assertRaises(r.RecoveryError):
                self.recover(apply=True)
        self.assertEqual(self.aws.writes, [])

    def test_cli_default_is_plan_and_does_not_mutate_ci_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plan.json"
            path.write_text(r.core.canonical(self.plan), encoding="utf-8")
            stream = io.StringIO()
            with patch.object(r, "Recovery", return_value=self.runner), patch("sys.stdout", stream):
                code = r.main(["--plan", str(path), "--owner", BINDING["owner"], "--source", BINDING["source"]])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stream.getvalue())["action"], "plan")
            self.assertEqual(self.aws.writes, [])

    def test_cli_never_exports_provider_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plan.json"
            path.write_text(r.core.canonical(self.plan), encoding="utf-8")
            self.freshness.side_effect = RuntimeError("private-credentials-and-provider-output")
            stream = io.StringIO()
            with patch.object(r, "Recovery", return_value=self.runner), patch("sys.stderr", stream):
                code = r.main(["--plan", str(path), "--owner", BINDING["owner"], "--source", BINDING["source"]])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(stream.getvalue()), {"ok": False, "code": "recovery-unconfirmed"})

    def test_freshness_invokes_owning_guard_without_skip_or_branch_override(self):
        with patch.object(r.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            r.assert_fresh_source(BINDING["source"])
        argv = run.call_args_list[0].args[0]
        self.assertIn(str(r.REPO_ROOT / "scripts/v1/assert-production-source-freshness.ps1"), argv)
        self.assertNotIn("-SkipFetch", argv)
        self.assertNotIn("-BranchName", argv)
        self.assertEqual(run.call_args_list[1].args[0][-4:], ["merge-base", "--is-ancestor", BINDING["source"], "HEAD"])

    def test_aws_consistent_read_boolean_is_a_flag_not_a_value(self):
        with patch.object(r.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "{}", "")) as run:
            r.Aws()("dynamodb", "get-item", consistent_read=True)
        argv = run.call_args.args[0]
        self.assertIn("--consistent-read", argv)
        self.assertNotIn("True", argv)

    def test_private_timeout_consumed_and_deadline_bounded(self):
        with patch.object(r.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "{}", "")) as run:
            r.Aws()("sqs", "get-queue-attributes", _timeout=3.5, queue_url="fixture")
        self.assertEqual(run.call_args.kwargs["timeout"], 3.5)
        self.assertNotIn("--_timeout", run.call_args.args[0])
        self.assertNotIn("---timeout", run.call_args.args[0])
        self.assertNotIn("3.5", run.call_args.args[0])

    def test_transport_errors_preserve_core_uncertain_write_contract(self):
        with patch.object(r.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "private", "secret")):
            with self.assertRaisesRegex(r.core.CutoverError, "^aws-call-unconfirmed$"):
                r.Aws()("sqs", "set-queue-attributes")


if __name__ == "__main__":
    unittest.main()
