"""Offline AWS/lock failure injection; these tests are not live rollout evidence."""
import copy
import io
import json
import re
import subprocess
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from scripts.v1 import account_write_cutover as c


BINDING = {"owner": "ci-deploy:123:2", "source": "a" * 40, "runId": "123", "runAttempt": "2"}
TG = f"arn:aws:elasticloadbalancing:{c.REGION}:{c.ACCOUNT}:targetgroup/academy-v1-api-tg/1234567890123456"


def plan(required=True):
    value = {"version": 1, "binding": BINDING, "required": required, "anchor": c.ANCHOR,
             "listener": c.LISTENER, "rules": c.route_rules() if required else [],
             "candidate": {"gitSha": "a" * 40, "hash": "c" * 64, "images": {}},
             "previous": {"gitSha": "b" * 40, "hash": "d" * 64, "images": {}},
             "oldFleet": {}, "queues": [], "targetGroups": [TG]}
    for index, repo in enumerate(c.COMPONENTS):
        value["candidate"]["images"][repo] = {"digest": "sha256:" + "c" * 64, "source": "a" * 40}
        value["previous"]["images"][repo] = {"digest": "sha256:" + "d" * 64, "source": "b" * 40}
        if required:
            value["oldFleet"][repo] = [f"i-{index + 1:017x}"]
            name = c.COMPONENTS[repo][2]
            if name:
                value["queues"].append({"name": name,
                    "url": f"https://sqs.{c.REGION}.amazonaws.com/{c.ACCOUNT}/{name}",
                    "arn": f"arn:aws:sqs:{c.REGION}:{c.ACCOUNT}:{name}",
                    "original": {"Version": "2012-10-17", "Statement": [{"Sid": "Existing",
                        "Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::809466760795:root"},
                        "Action": "sqs:SendMessage", "Resource": f"arn:aws:sqs:{c.REGION}:{c.ACCOUNT}:{name}"}]}})
    value["id"] = c.digest(value)
    return value


class Lock:
    def __init__(self):
        self.state = None
        self.live = True
        self.events = []

    def load_window(self, table, owner):
        c.require(self.live and table == c.TABLE and owner == BINDING["owner"], "lock-owner")
        return copy.deepcopy(self.state)

    def save_window(self, table, owner, state, expected=None):
        self.load_window(table, owner)
        c.require(expected == self.state, "lock-cas")
        self.state = copy.deepcopy(state)
        self.events.append(("save", copy.deepcopy(state)))

    def clear_window(self, table, owner, expected):
        self.load_window(table, owner)
        c.require(self.state == expected, "lock-cas")
        self.events.append(("clear", copy.deepcopy(expected)))
        self.state = None


class FakeAws:
    def __init__(self, value, lock):
        self.plan, self.lock = value, lock
        self.rules = [{"Priority": "default", "RuleArn": "default", "Actions": [{"Type": "forward", "TargetGroupArn": TG}]}]
        self.tags = {}
        self.policies = {row["url"]: copy.deepcopy(row["original"]) for row in value["queues"]}
        self.members = copy.deepcopy(value["oldFleet"])
        self.images = {instance: value["previous"]["images"][repo]["digest"]
                       for repo, instances in self.members.items() for instance in instances}
        self.terminated = set()
        self.events = []
        self.lost = None
        self.failed = None
        self.inflight = "0"
        self.refresh = "Successful"
        self.marker = 110
        self.errors = 0
        self.single = True
        self.running = True
        self.bad_tags = False
        self.target_draining = None
        self.detached = set()
        self.counts = (1, 1)
        self.queue_names = copy.deepcopy(c.QUEUE_NAMES)

    def mutation(self, operation):
        c.require(self.lock.state is not None and self.lock.state["intent"]["op"] == {
            "create-rule": "create-rule", "delete-rule": "delete-rule",
            "set-queue-attributes": self.lock.state["intent"]["op"],
        }[operation], "intent-missing-before-mutation")
        self.events.append((operation, copy.deepcopy(self.lock.state["intent"])))

    def __call__(self, service, operation, **args):
        if self.failed == operation:
            raise c.CutoverError("aws-call-unconfirmed")
        if operation == "get-caller-identity":
            return {"Account": c.ACCOUNT, "Arn": f"arn:aws:sts::{c.ACCOUNT}:assumed-role/academy-gha-ecr-build/session"}
        if operation == "describe-listeners":
            return {"Listeners": [{"ListenerArn": c.LISTENER, "Port": 443, "Protocol": "HTTPS"}]}
        if operation == "describe-rules":
            return {"Rules": copy.deepcopy(self.rules)}
        if operation == "describe-tags":
            return {"TagDescriptions": [{"ResourceArn": args["resource_arns"], "Tags": copy.deepcopy(self.tags[args["resource_arns"]])}]}
        if operation == "create-rule":
            self.mutation(operation)
            arn = c.LISTENER.replace(":listener/", ":listener-rule/") + "/" + str(args["priority"])
            actions = copy.deepcopy(args["actions"])
            actions[0]["Order"] = 1  # actual ALB describe includes this optional field
            self.rules.append({"RuleArn": arn, "Priority": str(args["priority"]), "Conditions": copy.deepcopy(args["conditions"]), "Actions": actions})
            self.tags[arn] = copy.deepcopy(args["tags"])
            if self.bad_tags:
                self.tags[arn][-1]["Value"] = "foreign-run"
        elif operation == "delete-rule":
            self.mutation(operation)
            self.rules = [row for row in self.rules if row["RuleArn"] != args["rule_arn"]]
        elif operation == "get-queue-url":
            return {"QueueUrl": f"https://sqs.{c.REGION}.amazonaws.com/{c.ACCOUNT}/{args['queue_name']}"}
        elif operation == "get-queue-attributes":
            row = next(row for row in self.plan["queues"] if row["url"] == args["queue_url"])
            return {"Attributes": {"QueueArn": row["arn"], "Policy": json.dumps(self.policies[row["url"]]),
                                   "ApproximateNumberOfMessagesNotVisible": self.inflight}}
        elif operation == "set-queue-attributes":
            self.mutation(operation)
            self.policies[args["queue_url"]] = json.loads(args["attributes"]["Policy"] or "{}")
        elif operation == "describe-auto-scaling-groups":
            asg = args["auto_scaling_group_names"]
            repo = next(repo for repo in c.COMPONENTS if c.COMPONENTS[repo][0] == asg)
            return {"AutoScalingGroups": [{"AutoScalingGroupName": asg, "DesiredCapacity": len(self.members[repo]),
                "Instances": [{"InstanceId": instance, "LifecycleState": "InService", "HealthStatus": "Healthy"}
                              for instance in self.members[repo]]}]}
        elif operation == "describe-instance-refreshes":
            return {"InstanceRefreshes": [{"Status": self.refresh}]}
        elif operation == "describe-instances":
            if "filters" in args:
                asg = args["filters"][0]["Values"][0]
                repo = next(repo for repo in c.COMPONENTS if c.COMPONENTS[repo][0] == asg)
                return {"Reservations": [{"Instances": [{"InstanceId": instance} for instance in
                    sorted(set(self.members[repo]) | (self.detached if repo == "academy-api" else set()))]}]}
            return {"Reservations": [{"Instances": [{"InstanceId": instance, "State": {
                "Name": "terminated" if instance in self.terminated else "running"}} for instance in args["instance_ids"]]}]}
        elif operation == "describe-target-health":
            rows = [{"Target": {"Id": instance}, "TargetHealth": {"State": "healthy"}}
                    for instance in self.members["academy-api"]]
            if self.target_draining:
                rows.append({"Target": {"Id": self.target_draining}, "TargetHealth": {"State": "draining"}})
            return {"TargetHealthDescriptions": rows}
        else:
            raise AssertionError((service, operation, args))
        if self.lost == operation:
            self.lost = None
            raise c.CutoverError("aws-call-unconfirmed")
        return {}

    def observe(self, aws, repo, instance, since):
        return {"running": self.running, "stable": True, "digests": [f"{c.REGISTRY}/{repo}@{self.images[instance]}"],
                "pauseAt": self.marker, "observedAt": 120, "singleLoop": self.single,
                "queueNames": self.queue_names,
                "terminalCount": self.counts[0], "ackCount": self.counts[1], "errorCount": self.errors}

    def promote(self):
        for index, repo in enumerate(c.COMPONENTS):
            old = self.members[repo]
            new = f"i-{index + 11:017x}"
            self.members[repo] = [new]
            self.images[new] = self.plan["candidate"]["images"][repo]["digest"]
            self.terminated.update(old)


class CutoverTests(unittest.TestCase):
    def setUp(self):
        self.plan = plan()
        self.lock = Lock()
        self.aws = FakeAws(self.plan, self.lock)
        self.window = self.new_window()

    def new_window(self):
        return c.Window(self.aws, self.lock, c.TABLE, self.plan, BINDING, self.aws.observe,
                        clock=lambda: 100, wait=lambda _: None)

    def opened(self):
        self.window.open()
        return self.window

    def matches(self, method, path):
        # ALB applies URI normalization first; this models normal percent decoding.
        path = unquote(urlsplit(path).path)
        return any(method in row["conditions"][0]["HttpRequestMethodConfig"]["Values"]
                   and any(re.fullmatch(pattern, path) for pattern in row["conditions"][1]["PathPatternConfig"]["RegexValues"])
                   for row in c.route_rules())

    def test_routes_block_account_variants(self):
        for method, path in [
            ("POST", "/api/v1/students/"), ("PATCH", "/api/v1/students/001/"),
            ("DELETE", "/api/v1/students/+01/"), ("PUT", "/api/v1/students/%31/"),
            ("PATCH", "/api/v1/students/opaque-key/"), ("PATCH", "/api/v1/students/me/"),
            ("PATCH", "/api/v1/student/me/"), ("PATCH", "/api/v1/core/profile/update_me/"),
            ("POST", "/api/v1/students/registration_requests/"),
            ("POST", "/api/v1/students/registration_requests/bulk_approve/"),
            ("POST", "/api/v1/students/registration_requests/+01/resolve_deleted/"),
            ("POST", "/api/v1/students/registration_requests/1/approve/"),
            ("POST", "/api/v1/students/password_reset_send/"),
            ("POST", "/api/v1/auth/account-recovery/dispatch/"),
            ("POST", "/api/v1/core/profile/change-password/"),
        ] + [("POST", f"/api/v1/students/{action}/") for action in ("bulk_create", "bulk_create_from_excel",
              "bulk_resolve_conflicts", "bulk_delete", "bulk_restore", "bulk_permanent_delete", "deleted_duplicates_fix")]:
            with self.subTest(method=method, path=path):
                self.assertTrue(self.matches(method, path))

    def test_normal_routes_remain_available(self):
        for method, path in [
            ("GET", "/api/v1/students/1/"), ("POST", "/api/v1/students/me/activity/"),
            ("POST", "/api/v1/students/me/activity/homework-open/"),
            ("POST", "/api/v1/students/me/activity/exam-result-open/"),
            ("POST", "/api/v1/students/me/support-session/end/"),
            ("POST", "/api/v1/students/1/support-session/"),
            ("POST", "/api/v1/students/1/support-sessions/uuid/end/"),
            ("POST", "/api/v1/students/1/enrollment-matrix/toggle/"),
            ("POST", "/api/v1/students/1/add_tag/"), ("POST", "/api/v1/enrollments/"),
            ("PATCH", "/api/v1/students/account-password-settings/"),
            ("PATCH", "/api/v1/students/registration_requests/settings/"),
            ("POST", "/api/v1/students/registration_requests/1/reject/"),
            ("POST", "/api/v1/students/registration_requests/bulk_reject/"),
            ("POST", "/api/v1/auth/token/"), ("GET", "/landing/resources"),
            ("PATCH", "/api/v1/students/tags/"), ("PATCH", "/api/v1/students/custom-fields/"),
        ]:
            with self.subTest(method=method, path=path):
                self.assertFalse(self.matches(method, path))

    def test_reserved_prefixes_do_not_create_pk_holes(self):
        for word in ("tags", "custom-fields", "registration_requests", "account-password-settings"):
            self.assertFalse(self.matches("PATCH", f"/api/v1/students/{word}/"))
            for key in (word + "x", word[:-1], "x" + word, word.upper()):
                self.assertTrue(self.matches("PATCH", f"/api/v1/students/{key}/"))

    def test_alb_limits_and_supported_regex(self):
        self.assertLess(len(c.route_rules()), 30)
        for rule in c.route_rules():
            methods = rule["conditions"][0]["HttpRequestMethodConfig"]["Values"]
            patterns = rule["conditions"][1]["PathPatternConfig"]["RegexValues"]
            self.assertLessEqual(len(methods), 3)
            self.assertLessEqual(len(patterns), 3)
            self.assertLessEqual(len(methods) + len(patterns), 5)
            for pattern in patterns:
                self.assertLessEqual(len(pattern.encode()), 128)
                self.assertNotIn("(?", pattern)

    def test_open_drain_promote_close_normal_and_reload(self):
        self.opened().drain()
        self.assertEqual(self.lock.state["phase"], "drained")
        self.aws.promote()
        self.new_window().close()
        self.assertIsNone(self.lock.state)
        self.assertEqual(len(self.aws.rules), 1)
        for queue in self.plan["queues"]:
            self.assertEqual(self.aws.policies[queue["url"]], queue["original"])
        self.new_window().close()  # repeat after successful cleanup

    def test_receive_only_deny_preserves_ack_and_visibility(self):
        self.opened()
        for queue in self.plan["queues"]:
            own = self.aws.policies[queue["url"]]["Statement"][-1]
            self.assertEqual(own["Action"], "sqs:ReceiveMessage")
            self.assertEqual(own["Resource"], queue["arn"])

    def test_intent_precedes_every_mutation_and_same_lock_is_cleared_last(self):
        self.opened().drain()
        self.aws.promote()
        self.window.close()
        self.assertTrue(self.aws.events)
        self.assertEqual(self.lock.events[-1][0], "clear")
        self.assertEqual(self.lock.events[-1][1]["phase"], "restored")

    def test_lost_create_response_readback_avoids_duplicate(self):
        self.aws.lost = "create-rule"
        self.opened()
        self.assertEqual(len(self.aws.rules), len(self.plan["rules"]) + 1)
        self.assertEqual(self.lock.state["phase"], "open")

    def test_lost_queue_response_recovers_exact_write(self):
        self.aws.lost = "set-queue-attributes"
        self.opened()
        self.window.verify_pause()

    def test_partial_open_before_rollout_restores_previous_fleet(self):
        self.aws.failed = "set-queue-attributes"
        with self.assertRaises(c.CutoverError):
            self.window.open()
        self.assertIsNotNone(self.lock.state["intent"])
        self.aws.failed = None
        self.new_window().close()
        self.assertIsNone(self.lock.state)
        self.assertEqual(self.lock.events[-1][1]["restorationRuntime"], "previous")

    def test_retry_open_reuses_durable_state(self):
        self.aws.failed = "set-queue-attributes"
        with self.assertRaises(c.CutoverError):
            self.window.open()
        self.aws.failed = None
        self.new_window().open()
        self.assertEqual(len(self.aws.rules), len(self.plan["rules"]) + 1)
        self.assertIsNone(self.lock.state["intent"])

    def test_missing_atomic_tags_are_not_adopted_or_deleted(self):
        self.aws.bad_tags = True
        with self.assertRaisesRegex(c.CutoverError, "foreign-rule"):
            self.window.open()
        with self.assertRaises(c.CutoverError):
            self.new_window().close()
        self.assertFalse(any(op == "delete-rule" for op, _ in self.aws.events))
        self.assertIsNotNone(self.lock.state)

    def test_foreign_queue_changes_survive_restore(self):
        self.opened()
        foreign = {"Sid": "OtherOwner", "Effect": "Allow", "Principal": "*", "Action": "sqs:SendMessage", "Resource": "other"}
        first = self.plan["queues"][0]
        self.aws.policies[first["url"]]["Statement"].append(foreign)
        self.window.close()
        self.assertIn(foreign, self.aws.policies[first["url"]]["Statement"])
        self.assertEqual(self.aws.policies[first["url"]]["Statement"][0], first["original"]["Statement"][0])

    def test_modified_own_sid_is_not_removed(self):
        self.opened()
        self.aws.policies[self.plan["queues"][0]["url"]]["Statement"][-1]["Action"] = "sqs:*"
        with self.assertRaisesRegex(c.CutoverError, "owned-statement-drift"):
            self.window.close()
        self.assertIsNotNone(self.lock.state)

    def test_approximate_zero_alone_never_proves_drain(self):
        self.opened()
        self.aws.marker = 99
        with self.assertRaisesRegex(c.CutoverError, "not-quiescent"):
            self.window.drain()
        self.assertEqual(self.lock.state["phase"], "open")

    def test_running_single_loop_source_and_ack_required(self):
        self.opened()
        for key, value in (("running", False), ("single", False), ("errors", 1), ("inflight", "1")):
            original = getattr(self.aws, key)
            setattr(self.aws, key, value)
            with self.subTest(key=key), self.assertRaises(c.CutoverError):
                self.window.drain()
            setattr(self.aws, key, original)
        instance = self.plan["oldFleet"]["academy-tools-worker"][0]
        self.aws.images[instance] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(c.CutoverError, "digest-mismatch"):
            self.window.drain()

    def test_drain_waits_for_actual_next_backoff_then_rechecks_pause(self):
        self.opened()
        self.aws.marker = 0
        waits = []
        def wait(seconds):
            waits.append(seconds)
            self.aws.marker = 110
        self.window.wait = wait
        self.window.drain()
        self.assertEqual(waits, [10])
        self.assertEqual(self.lock.state["phase"], "drained")

    def test_scaled_old_worker_is_recorded_and_must_retire(self):
        self.opened()
        repo = "academy-tools-worker"
        extra = "i-000000000000000ff"
        self.aws.members[repo].append(extra)
        self.aws.images[extra] = self.plan["previous"]["images"][repo]["digest"]
        self.window.drain()
        self.assertIn(extra, self.lock.state["oldFleet"][repo])
        self.aws.promote()
        self.aws.terminated.remove(extra)
        with self.assertRaises(c.CutoverError):
            self.window.close()
        self.aws.terminated.add(extra)
        self.window.close()
        self.assertIsNone(self.lock.state)

    def test_scaled_previous_fleet_can_abort_without_snapshot_hold(self):
        self.opened()
        repo = "academy-tools-worker"
        extra = "i-000000000000000ff"
        self.aws.members[repo].append(extra)
        self.aws.images[extra] = self.plan["previous"]["images"][repo]["digest"]
        self.window.close()
        self.assertIsNone(self.lock.state)

    def test_detached_running_old_api_blocks_candidate_reopen(self):
        self.opened().drain()
        self.aws.promote()
        self.aws.detached.add("i-000000000000000ff")
        with self.assertRaises(c.CutoverError):
            self.window.close()
        self.assertIsNotNone(self.lock.state)

    def test_idle_counts_allowed_but_terminal_without_ack_rejected(self):
        self.opened()
        self.aws.counts = (1, 0)
        with self.assertRaisesRegex(c.CutoverError, "terminal-ack"):
            self.window.drain()
        self.aws.counts = (0, 0)
        self.window.drain()
        self.assertEqual(self.lock.state["phase"], "drained")

    def test_unknown_or_missing_queue_alias_refuses_drain_and_close(self):
        self.opened()
        self.aws.queue_names["AI_SQS_QUEUE_NAME_PREMIUM"] = "foreign-queue"
        with self.assertRaisesRegex(c.CutoverError, "queue-config"):
            self.window.drain()
        with self.assertRaisesRegex(c.CutoverError, "queue-config"):
            self.window.close()
        self.assertIsNotNone(self.lock.state)

    def test_partial_new_fleet_refuses_reopen(self):
        self.opened().drain()
        repo = "academy-api"
        new = "i-000000000000000aa"
        self.aws.members[repo] = [new]
        self.aws.images[new] = self.plan["candidate"]["images"][repo]["digest"]
        with self.assertRaises(c.CutoverError):
            self.window.close()
        self.assertIsNotNone(self.lock.state)
        self.assertEqual(len(self.aws.rules), len(self.plan["rules"]) + 1)

    def test_old_instances_and_draining_targets_must_be_retired(self):
        self.opened().drain()
        self.aws.promote()
        old = self.plan["oldFleet"]["academy-api"][0]
        self.aws.terminated.remove(old)
        with self.assertRaises(c.CutoverError):
            self.window.close()
        self.aws.terminated.add(old)
        self.aws.target_draining = old
        with self.assertRaises(c.CutoverError):
            self.window.close()
        self.assertIsNotNone(self.lock.state)

    def test_live_refresh_prevents_close_even_with_healthy_new_instance(self):
        self.opened().drain()
        self.aws.promote()
        self.aws.refresh = "InProgress"
        with self.assertRaises(c.CutoverError):
            self.window.close()

    def test_inspect_clear_never_removes_live_admission_or_queue_policy(self):
        self.opened()
        count = len(self.aws.events)
        with self.assertRaises(c.CutoverError):
            self.window.close(inspect_only=True)
        self.assertEqual(len(self.aws.events), count)
        self.assertIsNotNone(self.lock.state)

    def test_lost_delete_response_and_retry_close(self):
        self.opened().drain()
        self.aws.promote()
        self.aws.lost = "delete-rule"
        self.window.close()
        self.assertIsNone(self.lock.state)

    def test_lost_owner_prevents_mutation(self):
        self.opened()
        count = len(self.aws.events)
        self.lock.live = False
        with self.assertRaises(c.CutoverError):
            self.window.close()
        self.assertEqual(len(self.aws.events), count)

    def test_different_run_or_plan_cannot_take_over_state(self):
        self.opened()
        changed = copy.deepcopy(self.plan)
        changed["previous"]["hash"] = "e" * 64
        changed["id"] = c.digest({key: value for key, value in changed.items() if key != "id"})
        replacement = c.Window(self.aws, self.lock, c.TABLE, changed, BINDING, self.aws.observe)
        with self.assertRaisesRegex(c.CutoverError, "window-binding"):
            replacement.close()
        with self.assertRaisesRegex(c.CutoverError, "plan-binding"):
            c.Window(self.aws, self.lock, c.TABLE, self.plan, {**BINDING, "owner": "ci-deploy:124:1"})

    def test_no_window_for_already_converged_release(self):
        value = plan(False)
        window = c.Window(self.aws, self.lock, c.TABLE, value, BINDING)
        window.open()
        window.drain()
        window.close()
        self.assertEqual(self.aws.events, [])
        self.assertEqual(self.lock.events, [])

    def test_noop_cannot_hide_preexisting_window(self):
        self.opened()
        window = c.Window(self.aws, self.lock, c.TABLE, plan(False), BINDING)
        with self.assertRaises(c.CutoverError):
            window.open()

    def test_aws_wrapper_is_finite_json_and_errors_do_not_expose_output(self):
        with patch.object(c.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "{}", "")) as run:
            c.Aws()("ec2", "describe-instances", instance_ids=["i-1", "i-2"])
            argv = run.call_args.args[0]
            self.assertEqual(argv[-3:], ["--instance-ids", "i-1", "i-2"])
            self.assertIn(c.REGION, argv)
            self.assertEqual(run.call_args.kwargs["timeout"], 60)
        with patch.object(c.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "private-body", "private-secret")):
            with self.assertRaisesRegex(c.CutoverError, "^aws-call-unconfirmed$"):
                c.Aws()("sqs", "set-queue-attributes")

    def test_ci_exact_context(self):
        env = {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": BINDING["source"]}
        with patch.dict(c.os.environ, env), patch.object(c, "resolve", return_value=BINDING["source"]), patch.object(c, "git", return_value=(0, BINDING["source"])):
            self.assertEqual(c.ci_binding(BINDING["owner"], BINDING["source"]), BINDING)
            with self.assertRaisesRegex(c.CutoverError, "owner-mismatch"):
                c.ci_binding("ci-deploy:123:1", BINDING["source"])

    def test_remote_observer_has_no_env_body_or_receipt_export(self):
        script = c.remote_observer("academy-ai-worker-cpu", 100)
        compile(script.split("\n", 1)[1].rsplit("\n", 1)[0], "observer", "exec")
        self.assertNotIn("Config.Env", script)
        self.assertNotIn("receipt_handle", script)
        self.assertNotIn("print(logs", script)
        self.assertIn("p.stdout+p.stderr", script)

    def test_actual_observer_code_filters_stderr_log_body_and_queue_names(self):
        repo = "academy-ai-worker-cpu"
        code = c.remote_observer(repo, 100).split("\n", 1)[1].rsplit("\n", 1)[0]
        state = {"Running": True, "StartedAt": "fixed", "Pid": 7}
        lines = "\n".join("1970-01-01T00:01:50.000000000Z " + text for text in (
            "1970-01-01 00:01:50 [WARNING] [AI-SQS-WORKER-CPU] SQS unavailable, waiting 60s",
            "AI_JOB_SQS_ACK | job_id=private-job | queue=academy-v1-ai-queue | message_id=private-id",
            "SQS_JOB_COMPLETED | job_id=private-job", "customer-body-and-secret",
        ))
        def run(argv, **kwargs):
            if argv[:2] == ["docker", "logs"]:
                return subprocess.CompletedProcess(argv, 0, "", lines)
            if argv[:2] == ["docker", "top"]:
                output = "COMMAND\npython -m apps.worker.ai_worker.sqs_main_cpu"
            elif argv[:2] == ["docker", "exec"]:
                output = json.dumps(c.QUEUE_NAMES)
            elif argv[:3] == ["docker", "image", "inspect"]:
                output = json.dumps([f"{c.REGISTRY}/{repo}@sha256:" + "d" * 64])
            elif "{{.Image}}" in argv:
                output = "sha256:local-image"
            else:
                output = json.dumps(state)
            return subprocess.CompletedProcess(argv, 0, output, "")
        stream = io.StringIO()
        with patch.object(c.subprocess, "run", side_effect=run), patch("sys.stdout", stream), patch.object(c.time, "time", return_value=120):
            exec(compile(code, "owned-observer", "exec"), {})
        value = json.loads(stream.getvalue())
        c.verify_observation(value, repo, "sha256:" + "d" * 64, 100, True)
        self.assertEqual((value["terminalCount"], value["ackCount"]), (1, 1))
        self.assertNotIn("private", stream.getvalue())
        self.assertNotIn("customer-body", stream.getvalue())

    def test_policy_drift_after_intent_is_not_overwritten(self):
        self.opened()
        queue = self.plan["queues"][0]
        observed = copy.deepcopy(self.aws.policies[queue["url"]])
        foreign = {"Sid": "new-owner", "Effect": "Allow", "Action": "sqs:SendMessage", "Resource": "other"}
        self.aws.policies[queue["url"]]["Statement"].append(foreign)
        count = len(self.aws.events)
        with self.assertRaisesRegex(c.CutoverError, "concurrent-drift"):
            self.window.write_policy(queue, observed, queue["original"])
        self.assertEqual(len(self.aws.events), count)
        self.assertIn(foreign, self.aws.policies[queue["url"]]["Statement"])

    def test_required_uses_each_image_source_not_release_main_sha(self):
        raw = {"schemaVersion": 1, "status": "successful", "complete": True, "gitSha": "a" * 40,
               "images": {repo: {"digest": "sha256:" + "d" * 64, "tag": "sha-" + "a" * 40, "source": "built"} for repo in c.COMPONENTS}}
        raw["images"]["academy-tools-worker"]["tag"] = "sha-" + "b" * 40
        candidate = copy.deepcopy(raw)
        candidate.update(status="candidate", complete=False)
        candidate["images"]["academy-tools-worker"]["tag"] = "sha-" + "a" * 40
        def ancestry(older, newer):
            return not (older == c.ANCHOR and newer == "b" * 40)
        with patch.object(c, "resolve", side_effect=lambda ref: ref), patch.object(c, "ancestor", side_effect=ancestry):
            value = c.make_plan(self.aws, BINDING, candidate, raw, c.ANCHOR)
        self.assertTrue(value["required"])
        self.assertEqual(value["previous"]["images"]["academy-tools-worker"]["source"], "b" * 40)

    def test_unresolved_or_pretransition_candidate_source_fails_closed(self):
        raw = {"schemaVersion": 1, "status": "candidate", "complete": False, "gitSha": BINDING["source"],
               "images": {repo: {"digest": "sha256:" + "c" * 64, "tag": "digest-only"} for repo in c.COMPONENTS}}
        with patch.object(c, "resolve", side_effect=lambda ref: ref), patch.object(c, "ancestor", return_value=True):
            with self.assertRaisesRegex(c.CutoverError, "source-unresolved"):
                c.normalize_manifest(raw, True, BINDING["source"], c.ANCHOR)
        for item in raw["images"].values():
            item["tag"] = "sha-" + "a" * 40
        with patch.object(c, "resolve", side_effect=lambda ref: ref), patch.object(c, "ancestor", side_effect=lambda old, new: old != c.ANCHOR):
            with self.assertRaisesRegex(c.CutoverError, "pre-transition"):
                c.normalize_manifest(raw, True, BINDING["source"], c.ANCHOR)


if __name__ == "__main__":
    unittest.main()
