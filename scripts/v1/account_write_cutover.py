#!/usr/bin/env python3
"""CI-owned, finite account-write window. Production approval belongs to the workflow.

No credentials, message bodies, application environment, or raw AWS errors are logged.
An interrupted window remains on the shared lock until its exact owner restores it.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

if __package__:
    from . import deployment_lock
else:
    import deployment_lock

REGION = "ap-northeast-2"
ACCOUNT = "809466760795"
REGISTRY = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"
LISTENER = (f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:listener/app/"
            "academy-v1-api-alb/6a077c1a400bc485/f91a3b4a87dc5e34")
ANCHOR = "5bf4371d4c3f9b702a322dfb0dba2debb8bc95e9"
BOOTSTRAP = "56df8cbcb"  # resolved uniquely by Git, never treated as a runtime digest
TABLE = "academy-v1-video-job-lock"
COMPONENTS = {
    "academy-api": ("academy-v1-api-asg", "academy-api", None),
    "academy-ai-worker-cpu": ("academy-v1-ai-worker-asg", "academy-ai-worker-cpu", "academy-v1-ai-queue"),
    "academy-tools-worker": ("academy-v1-tools-worker-asg", "academy-tools-worker", "academy-v1-tools-queue"),
}
ACTION = [{"Type": "fixed-response", "FixedResponseConfig": {
    "StatusCode": "503", "ContentType": "application/json",
    "MessageBody": '{"detail":"계정 정보 업데이트 중입니다. 잠시 후 다시 시도해주세요."}',
}}]
QUEUE_NAMES = {"AI_SQS_QUEUE_NAME_BASIC": "academy-v1-ai-queue",
               "AI_SQS_QUEUE_NAME_LITE": "academy-v1-ai-queue",
               "AI_SQS_QUEUE_NAME_PREMIUM": "academy-v1-ai-queue",
               "TOOLS_SQS_QUEUE_NAME": "academy-v1-tools-queue"}


class CutoverError(RuntimeError):
    """Safe, fixed diagnostic without provider output or customer data."""


def require(ok, code):
    if not ok:
        raise CutoverError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Aws:
    def __call__(self, service, operation, **args):
        command = ["aws", service, operation, "--region", REGION, "--output", "json", "--no-cli-pager"]
        for key, value in args.items():
            command.append("--" + key.replace("_", "-"))
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                command.extend(value)
            elif isinstance(value, (dict, list)):
                command.append(canonical(value))
            else:
                command.append(str(value))
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
            require(result.returncode == 0, "aws-call-unconfirmed")
            return json.loads(result.stdout or "{}")
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise CutoverError("aws-call-unconfirmed") from None


def git(*args):
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False, timeout=30)
    require(result.returncode in (0, 1), "git-metadata-unavailable")
    return result.returncode, result.stdout.strip()


def resolve(ref):
    require(bool(re.fullmatch(r"[0-9a-f]{8,40}", ref)), "invalid-source-ref")
    code, sha = git("rev-parse", "--verify", ref + "^{commit}")
    require(code == 0 and bool(re.fullmatch(r"[0-9a-f]{40}", sha)), "source-unresolved")
    return sha


def ancestor(older, newer):
    return git("merge-base", "--is-ancestor", older, newer)[0] == 0


def ci_binding(owner, source):
    run = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    require(bool(re.fullmatch(r"[1-9][0-9]*", run)) and bool(re.fullmatch(r"[1-9][0-9]*", attempt)), "ci-run-required")
    require(owner == f"ci-deploy:{run}:{attempt}", "ci-owner-mismatch")
    require(os.environ.get("GITHUB_SHA") == source and resolve(source) == source,
            "ci-source-mismatch")
    require(git("rev-parse", "HEAD")[1] == source, "checkout-source-mismatch")
    return {"owner": owner, "source": source, "runId": run, "runAttempt": attempt}


def normalize_manifest(raw, candidate, source, anchor):
    require(isinstance(raw, dict) and raw.get("schemaVersion") == 1, "manifest-schema")
    require(raw.get("status") == ("candidate" if candidate else "successful")
            and raw.get("complete") is (not candidate), "manifest-state")
    sha = raw.get("gitSha", "")
    require(resolve(sha) == sha and ancestor(sha, source), "manifest-source")
    if candidate:
        require(sha == source, "candidate-source-mismatch")
    images = {}
    for repo in COMPONENTS:
        item = raw.get("images", {}).get(repo, {})
        require(isinstance(item, dict) and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("digest", ""))), "manifest-digest")
        match = re.fullmatch(r"sha-([0-9a-f]{8,40})(?:-run-[1-9][0-9]*-[1-9][0-9]*)?", item.get("tag", ""))
        require(match is not None, "image-source-unresolved")
        image_sha = resolve(match.group(1))
        require(ancestor(image_sha, sha), "image-source-ancestry")
        if candidate:
            require(ancestor(anchor, image_sha), "candidate-image-pre-transition")
        images[repo] = {"digest": item["digest"], "source": image_sha}
    return {"gitSha": sha, "hash": digest(raw), "images": images}


def pk_patterns():
    """DRF accepts any single non-dot segment. Subtract reserved collection names.

    Prefix divergence expresses this without ALB's unsupported lookaheads.
    Each generated expression is separately bounded to the ALB length limit.
    """
    reserved = ("tags", "custom-fields", "registration_requests", "account-password-settings")
    trie = {}
    for word in reserved:
        node = trie
        for char in word:
            node = node.setdefault(char, {})
        node[None] = {}
    patterns = []

    def visit(node, prefix):
        children = sorted(key for key in node if key is not None)
        escaped = "".join(re.escape(char) for char in children)
        if children:
            patterns.append(re.escape(prefix) + "[^/." + escaped + "][^/.]*")
        else:
            patterns.append(re.escape(prefix) + "[^/.]+")
        if prefix and None not in node:
            patterns.append(re.escape(prefix))
        for char in children:
            visit(node[char], prefix + char)

    visit(trie, "")
    result, batch = [], []
    for part in patterns:
        proposed = "^/api/v1/students/(" + "|".join(batch + [part]) + ")/$"
        if len(proposed.encode()) > 128:
            result.append("^/api/v1/students/(" + "|".join(batch) + ")/$")
            batch = []
        batch.append(part)
    if batch:
        result.append("^/api/v1/students/(" + "|".join(batch) + ")/$")
    return result


def route_rules():
    groups = [(["PATCH", "PUT", "DELETE"], pk_patterns())]
    post = [
        "/api/v1/students/", "/api/v1/students/registration_requests/",
        "/api/v1/students/registration_requests/bulk_approve/",
        "/api/v1/students/password_reset_send/", "/api/v1/auth/account-recovery/dispatch/",
        "/api/v1/core/change-password/", "/api/v1/core/profile/change-password/",
    ] + ["/api/v1/students/" + action + "/" for action in (
        "bulk_create", "bulk_create_from_excel", "bulk_resolve_conflicts", "bulk_delete",
        "bulk_restore", "bulk_permanent_delete", "deleted_duplicates_fix")]
    groups.append((["POST"], ["^" + re.escape(path) + "$" for path in post] + [
        r"^/api/v1/students/registration_requests/[^/.]+/approve/$",
        r"^/api/v1/students/registration_requests/[^/.]+/resolve_deleted/$",
    ]))
    groups.append((["PATCH"], [r"^/api/v1/student/me/$", r"^/api/v1/core/profile/update_me/$"]))
    rules = []
    for methods, patterns in groups:
        size = min(3, 5 - len(methods))
        for offset in range(0, len(patterns), size):
            selected = patterns[offset:offset + size]
            require(all(len(pattern.encode()) <= 128 for pattern in selected), "route-regex-too-long")
            rules.append({"priority": len(rules) + 1, "conditions": [
                {"Field": "http-request-method", "HttpRequestMethodConfig": {"Values": methods}},
                {"Field": "path-pattern", "PathPatternConfig": {"RegexValues": selected}},
            ]})
    return rules


def statements(policy):
    raw = policy.get("Statement", [])
    require(isinstance(raw, (dict, list)), "queue-policy-shape")
    result = [raw] if isinstance(raw, dict) else raw
    require(all(isinstance(row, dict) for row in result), "queue-policy-statement-shape")
    return result


def pause_statement(sid, arn):
    return {"Sid": sid, "Effect": "Deny", "Principal": "*", "Action": "sqs:ReceiveMessage", "Resource": arn}


def paused_policy(original, sid, arn):
    result = copy.deepcopy(original)
    require(not any(row.get("Sid") == sid for row in statements(result)), "queue-sid-collision")
    result.setdefault("Version", "2012-10-17")
    result["Statement"] = copy.deepcopy(statements(result)) + [pause_statement(sid, arn)]
    return result


def restored_policy(current, original, sid, arn):
    matches = [row for row in statements(current) if row.get("Sid") == sid]
    require(len(matches) <= 1, "queue-sid-duplicate")
    if not matches:
        return current
    require(matches == [pause_statement(sid, arn)], "queue-owned-statement-drift")
    result = copy.deepcopy(current)
    result["Statement"] = [row for row in statements(current) if row.get("Sid") != sid]
    # Exact original representation when unchanged; otherwise retain foreign edits.
    expected = paused_policy(original, sid, arn)
    return copy.deepcopy(original) if current == expected else result


def queue_attrs(aws, queue):
    attrs = aws("sqs", "get-queue-attributes", queue_url=queue["url"], attribute_names="All").get("Attributes", {})
    require(attrs.get("QueueArn") == queue["arn"], "queue-identity-mismatch")
    return attrs


def policy_from(attrs):
    try:
        value = json.loads(attrs.get("Policy") or "{}")
        require(isinstance(value, dict), "queue-policy-shape")
        statements(value)
        return value
    except ValueError:
        raise CutoverError("queue-policy-invalid") from None


def verify_aws(aws):
    identity = aws("sts", "get-caller-identity")
    require(identity.get("Account") == ACCOUNT and identity.get("Arn", "").startswith(
        f"arn:aws:sts::{ACCOUNT}:assumed-role/academy-gha-ecr-build/"), "production-ci-role-required")


def fleet(aws, repo):
    asg = COMPONENTS[repo][0]
    groups = aws("autoscaling", "describe-auto-scaling-groups", auto_scaling_group_names=asg).get("AutoScalingGroups", [])
    require(len(groups) == 1 and groups[0].get("AutoScalingGroupName") == asg, "fleet-identity")
    group = groups[0]
    members = group.get("Instances", [])
    desired = group.get("DesiredCapacity", 0)
    require(isinstance(desired, int) and desired >= 1 and len(members) == desired, "fleet-capacity-not-converged")
    require(all(row.get("LifecycleState") == "InService" and row.get("HealthStatus") == "Healthy"
                and re.fullmatch(r"i-[0-9a-f]{8,17}", row.get("InstanceId", "")) for row in members), "fleet-not-healthy")
    return sorted(row["InstanceId"] for row in members)


def live_tagged_instances(aws, repo):
    result = aws("ec2", "describe-instances", filters=[
        {"Name": "tag:aws:autoscaling:groupName", "Values": [COMPONENTS[repo][0]]},
        {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped", "shutting-down"]},
    ])
    instances = [row for reservation in result.get("Reservations", []) for row in reservation.get("Instances", [])]
    require(all(re.fullmatch(r"i-[0-9a-f]{8,17}", row.get("InstanceId", "")) for row in instances), "ec2-instance-identity")
    return sorted(row["InstanceId"] for row in instances)


def make_plan(aws, binding, candidate_raw, previous_raw, anchor):
    verify_aws(aws)
    anchor = resolve(anchor)
    candidate = normalize_manifest(candidate_raw, True, binding["source"], anchor)
    previous = normalize_manifest(previous_raw, False, binding["source"], anchor)
    required = not all(ancestor(anchor, item["source"]) for item in previous["images"].values())
    plan = {"version": 1, "binding": binding, "required": required, "anchor": anchor,
            "candidate": candidate, "previous": previous, "listener": LISTENER, "queues": [], "rules": [], "oldFleet": {}}
    if required:
        for repo in COMPONENTS:
            plan["oldFleet"][repo] = fleet(aws, repo)
            if repo != "academy-api":
                require(ancestor(resolve(BOOTSTRAP), previous["images"][repo]["source"]), "old-worker-pause-bootstrap-missing")
        listeners = aws("elbv2", "describe-listeners", listener_arns=LISTENER).get("Listeners", [])
        require(len(listeners) == 1 and listeners[0].get("ListenerArn") == LISTENER
                and listeners[0].get("Port") == 443 and listeners[0].get("Protocol") == "HTTPS", "listener-identity")
        rules = route_rules()
        existing = aws("elbv2", "describe-rules", listener_arn=LISTENER).get("Rules", [])
        require(existing and len(existing) + len(rules) <= 100, "listener-rule-capacity")
        require(all(row.get("Priority") == "default" or int(row["Priority"]) > len(rules)
                    for row in existing), "listener-priority-conflict")
        plan["rules"] = rules
        plan["targetGroups"] = sorted({action["TargetGroupArn"] for row in existing
            for action in row.get("Actions", []) if action.get("Type") == "forward" and action.get("TargetGroupArn")})
        require(len(plan["targetGroups"]) == 1, "api-target-group-ambiguous")
        for _, _, name in COMPONENTS.values():
            if name is None:
                continue
            url = aws("sqs", "get-queue-url", queue_name=name, queue_owner_aws_account_id=ACCOUNT).get("QueueUrl")
            require(url == f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/{name}", "queue-url-mismatch")
            queue = {"name": name, "url": url, "arn": f"arn:aws:sqs:{REGION}:{ACCOUNT}:{name}"}
            queue["original"] = policy_from(queue_attrs(aws, queue))
            plan["queues"].append(queue)
    plan["id"] = digest(plan)
    return plan


def validate_plan(plan, binding):
    require(isinstance(plan, dict) and plan.get("version") == 1 and plan.get("binding") == binding, "plan-binding-mismatch")
    unsigned = {key: value for key, value in plan.items() if key != "id"}
    require(plan.get("id") == digest(unsigned) and plan.get("listener") == LISTENER, "plan-integrity")
    require(isinstance(plan.get("required"), bool), "plan-required-invalid")
    if plan["required"]:
        require(plan.get("rules") == route_rules() and set(plan.get("oldFleet", {})) == set(COMPONENTS), "plan-targets-mismatch")
        require([row["name"] for row in plan.get("queues", [])] == [row[2] for row in COMPONENTS.values() if row[2]], "plan-queues-mismatch")
        for queue in plan["queues"]:
            name = queue["name"]
            require(queue["url"] == f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/{name}"
                    and queue["arn"] == f"arn:aws:sqs:{REGION}:{ACCOUNT}:{name}", "plan-queue-identity")


def remote_observer(repo, since):
    """Only Docker identity and filtered log counters leave the host; no env/body dump."""
    container = COMPONENTS[repo][1]
    # All interpolated values are fixed identifiers or a bounded integer timestamp.
    require(isinstance(since, int) and since > 0, "observation-time-invalid")
    code = '''import datetime,json,subprocess,time
def run(args,logs=False):
 p=subprocess.run(args,capture_output=True,text=True,timeout=35)
 if p.returncode: raise RuntimeError("observer-command")
 return p.stdout+p.stderr if logs else p.stdout
container=CONTAINER
before=json.loads(run(["docker","inspect","--format","{{json .State}}",container]))
image=run(["docker","inspect","--format","{{.Image}}",container]).strip()
digests=json.loads(run(["docker","image","inspect","--format","{{json .RepoDigests}}",image]))
processes=run(["docker","top",container,"-eo","args"])
queueNames=json.loads(run(["docker","exec",container,"python","-c",
 "import json,os; print(json.dumps({key:os.environ.get(key) for key in QUEUE_KEYS}))"])) if WORKER else {}
logs=run(["docker","logs","--since",str(SINCE),"--timestamps",container],logs=True) if WORKER else ""
if len(logs.encode())>1000000: raise RuntimeError("observer-log-bound")
pause=0
terminal=ack=errors=0
for line in logs.splitlines():
 if "SQS unavailable, waiting 60s" in line:
  stamp=line.split(" ",1)[0]
  stamp=stamp[:19]+"+00:00"
  pause=max(pause,int(datetime.datetime.fromisoformat(stamp).timestamp()))
 if "SQS_JOB_COMPLETED |" in line or "SQS_JOB_FAILED |" in line: terminal+=1
 if "AI_JOB_SQS_ACK |" in line: ack+=1
 if any(x in line for x in ("ACK_RETRY", "ACK_FAILED", "ACK_IDENTITY_MISSING", "CALLBACK_RETRY_DEFERRED", "SQS delete", "SQS_ACK_FAILED", "AI_JOB_SQS_DELETE_FAILED")): errors+=1
after=json.loads(run(["docker","inspect","--format","{{json .State}}",container]))
print(json.dumps({"observedAt":int(time.time()),"running":before.get("Running") is True and after.get("Running") is True,
 "stable":before.get("StartedAt")==after.get("StartedAt") and before.get("Pid")==after.get("Pid"),
 "digests":digests,"queueNames":queueNames,"pauseAt":pause,"terminalCount":terminal,"ackCount":ack,"errorCount":errors,
 "singleLoop":sum("WORKER_MODULE" in row for row in processes.splitlines())==1 if WORKER else True}))
'''
    module = "apps.worker.ai_worker.sqs_main_cpu" if repo == "academy-ai-worker-cpu" else "apps.worker.tools_worker.sqs_main"
    code = code.replace("CONTAINER", repr(container)).replace("SINCE", str(since)).replace("WORKER_MODULE", module).replace("WORKER", str(repo != "academy-api"))
    code = code.replace("QUEUE_KEYS", repr(tuple(QUEUE_NAMES)))
    return "python3 - <<'ACCOUNT_CUTOVER_OBSERVER'\n" + code + "\nACCOUNT_CUTOVER_OBSERVER"


def observe(aws, repo, instance, since):
    result = aws("ssm", "send-command", instance_ids=instance, document_name="AWS-RunShellScript",
                 parameters={"commands": [remote_observer(repo, since)], "executionTimeout": ["120"]}, timeout_seconds=180)
    command = result.get("Command", {}).get("CommandId", "")
    require(bool(re.fullmatch(r"[0-9a-f-]{36}", command)), "observer-command-unconfirmed")
    for _ in range(24):
        try:
            result = aws("ssm", "get-command-invocation", command_id=command, instance_id=instance)
        except CutoverError:
            time.sleep(5)
            continue
        if result.get("Status") == "Success":
            try:
                value = json.loads(result.get("StandardOutputContent", ""))
                require(isinstance(value, dict), "observer-result-invalid")
                return value
            except ValueError:
                raise CutoverError("observer-result-invalid") from None
        require(result.get("Status") in ("Pending", "InProgress", "Delayed"), "observer-failed")
        time.sleep(5)
    raise CutoverError("observer-timeout")


def verify_observation(value, repo, expected, since, draining):
    require(value.get("running") is True and value.get("stable") is True, "runtime-container-not-stable")
    require(f"{REGISTRY}/{repo}@{expected}" in value.get("digests", []), "runtime-digest-mismatch")
    if repo != "academy-api":
        require(value.get("queueNames") == QUEUE_NAMES, "runtime-queue-config-mismatch")
    if draining:
        require(value.get("singleLoop") is True and isinstance(value.get("pauseAt"), int)
                and isinstance(value.get("observedAt"), int) and value["pauseAt"] >= since
                and value["observedAt"] - 120 <= value["pauseAt"] <= value["observedAt"] + 5,
                "worker-not-quiescent")
        require(value.get("errorCount") == 0 and all(isinstance(value.get(key), int) and value[key] >= 0
            for key in ("terminalCount", "ackCount")) and value["ackCount"] >= value["terminalCount"],
            "worker-terminal-ack-unconfirmed")


class Window:
    def __init__(self, aws, lock, table, plan, binding, observer=observe, clock=time.time, wait=time.sleep):
        validate_plan(plan, binding)
        self.aws, self.lock, self.table, self.plan = aws, lock, table, plan
        self.owner, self.observer, self.clock = binding["owner"], observer, clock
        self.wait = wait
        self.state = None
        self.restoration_runtime = None

    @property
    def sid(self):
        return "AccountCutover" + self.plan["id"][:32]

    @property
    def tags(self):
        return [{"Key": "AcademyCutover", "Value": "account-write-v1"},
                {"Key": "AcademyCutoverOwner", "Value": self.owner}]

    def load(self):
        state = self.lock.load_window(self.table, self.owner)
        if state is not None:
            require(state.get("plan") == self.plan and state.get("sid") == self.sid, "window-binding-mismatch")
        self.state = state
        return state

    def save(self, **changes):
        previous = copy.deepcopy(self.state)
        state = copy.deepcopy(self.state)
        state.update(changes)
        self.lock.save_window(self.table, self.owner, state, expected=previous)
        self.state = state

    def owned_rules(self):
        found = {}
        rules = self.aws("elbv2", "describe-rules", listener_arn=LISTENER).get("Rules", [])
        planned = {str(row["priority"]): row for row in self.plan["rules"]}
        for rule in rules:
            priority = rule.get("Priority")
            if priority not in planned:
                continue
            arn = rule.get("RuleArn", "")
            expected_prefix = LISTENER.replace(":listener/", ":listener-rule/") + "/"
            require(arn.startswith(expected_prefix), "rule-listener-mismatch")
            descriptions = self.aws("elbv2", "describe-tags", resource_arns=arn).get("TagDescriptions", [])
            require(len(descriptions) == 1 and descriptions[0].get("ResourceArn") == arn, "rule-tags-unconfirmed")
            tags = {row["Key"]: row["Value"] for row in descriptions[0].get("Tags", [])}
            require(all(tags.get(row["Key"]) == row["Value"] for row in self.tags), "foreign-rule-priority-conflict")
            conditions = []
            for row in rule.get("Conditions", []):
                field = row.get("Field")
                key = "HttpRequestMethodConfig" if field == "http-request-method" else "PathPatternConfig"
                conditions.append({"Field": field, key: row.get(key)})
            actions = copy.deepcopy(rule.get("Actions"))
            if isinstance(actions, list):
                for action in actions:
                    require(action.get("Order", 1) == 1, "owned-rule-action-order")
                    action.pop("Order", None)
            require(sorted(conditions, key=canonical) == sorted(planned[priority]["conditions"], key=canonical)
                    and actions == ACTION, "owned-rule-drift")
            require(priority not in found, "rule-priority-duplicate")
            found[priority] = arn
        return found

    def verify_pause(self):
        require(len(self.owned_rules()) == len(self.plan["rules"]), "admission-not-closed")
        for queue in self.plan["queues"]:
            current = policy_from(queue_attrs(self.aws, queue))
            require([row for row in statements(current) if row.get("Sid") == self.sid]
                    == [pause_statement(self.sid, queue["arn"])], "queue-receive-not-paused")

    def checked_mutation(self, intent, operation, readback):
        # Revalidate owner and exact last state before writing durable intent.
        require(self.lock.load_window(self.table, self.owner) == self.state, "window-state-conflict")
        self.save(intent=intent)
        try:
            operation()
        except CutoverError:
            # A timeout may follow a successful write. Actual exact-state readback
            # decides; no blind duplicate creation or baseline overwrite.
            readback()
        else:
            readback()
        self.save(intent=None)

    def write_policy(self, queue, observed, expected):
        # SQS has no conditional policy update. Re-read immediately before the
        # write; preserve all observed foreign statements and reject drift.
        require(policy_from(queue_attrs(self.aws, queue)) == observed, "queue-policy-concurrent-drift")
        self.aws("sqs", "set-queue-attributes", queue_url=queue["url"],
                 attributes={"Policy": canonical(expected) if expected else ""})

    def open(self):
        self.load()
        if not self.plan["required"]:
            require(self.state is None, "unexpected-open-window")
            return
        if self.state is None:
            state = {"plan": self.plan, "sid": self.sid, "phase": "opening", "startedAt": int(self.clock()),
                     "oldFleet": copy.deepcopy(self.plan["oldFleet"]), "intent": None}
            self.lock.save_window(self.table, self.owner, state, expected=None)
            self.state = state
        require(self.state["phase"] in ("opening", "open", "drained"), "window-cannot-reopen")
        found = self.owned_rules()
        for rule in self.plan["rules"]:
            if str(rule["priority"]) in found:
                continue

            def read_rule(rule=rule):
                require(str(rule["priority"]) in self.owned_rules(), "admission-write-unconfirmed")

            self.checked_mutation({"op": "create-rule", "priority": rule["priority"]},
                lambda rule=rule: self.aws("elbv2", "create-rule", listener_arn=LISTENER, priority=rule["priority"],
                    conditions=rule["conditions"], actions=ACTION, tags=self.tags), read_rule)
        for queue in self.plan["queues"]:
            current = policy_from(queue_attrs(self.aws, queue))
            expected = paused_policy(queue["original"], self.sid, queue["arn"])
            if current == expected:
                continue
            require(current == queue["original"], "queue-policy-changed-before-open")
            self.checked_mutation({"op": "pause-queue", "name": queue["name"], "expectedPolicy": expected},
                lambda queue=queue, current=current, expected=expected: self.write_policy(queue, current, expected),
                lambda queue=queue, expected=expected: require(policy_from(queue_attrs(self.aws, queue)) == expected, "queue-pause-write-unconfirmed"))
        self.verify_pause()
        self.save(phase=self.state["phase"] if self.state["phase"] == "drained" else "open",
                  pausedAt=self.state.get("pausedAt", int(self.clock()) + 1), intent=None)

    def drain(self):
        deadline = time.monotonic() + 1200
        for attempt in range(120):
            require(time.monotonic() < deadline, "drain-deadline")
            try:
                self.drain_once()
                return
            except CutoverError as error:
                if str(error) not in ("worker-not-quiescent", "queue-inflight-not-zero", "fleet-capacity-not-converged",
                                      "fleet-not-healthy", "worker-fleet-transition"):
                    raise
                if attempt == 119 or time.monotonic() >= deadline:
                    raise
                self.wait(10)

    def drain_once(self):
        self.load()
        if not self.plan["required"]:
            require(self.state is None, "unexpected-open-window")
            return
        require(self.state is not None and self.state["phase"] in ("open", "drained"), "window-not-open")
        self.verify_pause()
        since = self.state["pausedAt"]
        observations = {}
        observed_fleet = copy.deepcopy(self.state["oldFleet"])
        for repo in COMPONENTS:
            if repo == "academy-api":
                continue
            current = fleet(self.aws, repo)
            require(live_tagged_instances(self.aws, repo) == current, "worker-fleet-transition")
            for instance in current:
                value = self.observer(self.aws, repo, instance, since)
                # Record additional old consumers before the pause check, even
                # when their first fresh backoff is still pending.
                verify_observation(value, repo, self.plan["previous"]["images"][repo]["digest"], since, False)
                if instance not in observed_fleet[repo]:
                    observed_fleet[repo] = sorted(set(observed_fleet[repo] + [instance]))
                    self.save(oldFleet=observed_fleet)
                verify_observation(value, repo, self.plan["previous"]["images"][repo]["digest"], since, True)
                observations[instance] = {key: value[key] for key in ("pauseAt", "terminalCount", "ackCount", "errorCount")}
            require(fleet(self.aws, repo) == current and live_tagged_instances(self.aws, repo) == current,
                    "worker-fleet-transition")
        for queue in self.plan["queues"]:
            require(queue_attrs(self.aws, queue).get("ApproximateNumberOfMessagesNotVisible") == "0", "queue-inflight-not-zero")
        self.verify_pause()
        self.save(phase="drained", drain={"observedAt": int(self.clock()), "workers": observations})

    def verify_retirement(self, previous=False):
        for repo in COMPONENTS:
            current = fleet(self.aws, repo)
            require(live_tagged_instances(self.aws, repo) == current, "live-runtime-outside-healthy-fleet")
            refreshes = self.aws("autoscaling", "describe-instance-refreshes", auto_scaling_group_name=COMPONENTS[repo][0], max_records=1).get("InstanceRefreshes", [])
            require(not refreshes or refreshes[0].get("Status") in ("Successful", "Cancelled", "RollbackSuccessful"), "fleet-refresh-not-converged")
            expected = self.plan["previous" if previous else "candidate"]["images"][repo]["digest"]
            changed = expected != self.plan["previous"]["images"][repo]["digest"]
            old = self.state["oldFleet"][repo]
            if changed and not previous:
                require(not set(old).intersection(current), "old-runtime-still-in-service")
                result = self.aws("ec2", "describe-instances", instance_ids=old)
                instances = [row for reservation in result.get("Reservations", []) for row in reservation.get("Instances", [])]
                require({row.get("InstanceId") for row in instances} == set(old)
                        and all(row.get("State", {}).get("Name") == "terminated" for row in instances), "old-runtime-not-terminated")
            for instance in current:
                value = self.observer(self.aws, repo, instance, self.state["startedAt"])
                verify_observation(value, repo, expected, self.state["startedAt"], False)
        for group in self.plan["targetGroups"]:
            targets = self.aws("elbv2", "describe-target-health", target_group_arn=group).get("TargetHealthDescriptions", [])
            current_api = set(fleet(self.aws, "academy-api"))
            active = [row for row in targets if row.get("TargetHealth", {}).get("State") != "unused"]
            require({row.get("Target", {}).get("Id") for row in active} == current_api
                    and all(row.get("TargetHealth", {}).get("State") == "healthy" for row in active), "api-targets-not-converged")

    def verify_restored(self):
        require(not self.owned_rules(), "admission-rule-remains")
        for queue in self.plan["queues"]:
            require(not any(row.get("Sid") == self.sid for row in statements(policy_from(queue_attrs(self.aws, queue)))), "queue-pause-remains")

    def close(self, inspect_only=False):
        self.load()
        if self.state is None:
            self.verify_restored() if self.plan["required"] else None
            return
        # Restore only a complete healthy candidate or previous runtime.
        # A partial open or failed rollout must not reopen a mixed fleet.
        try:
            self.verify_retirement()
            restoration = "candidate"
        except CutoverError:
            # A complete previous runtime permits a safe abort, including normal
            # scaling. A mixture passes neither complete-runtime check.
            self.verify_retirement(previous=True)
            restoration = "previous"
        self.restoration_runtime = restoration
        if inspect_only:
            self.verify_restored()
            self.lock.clear_window(self.table, self.owner, expected=self.state)
            self.state = None
            return
        self.save(phase="closing", restorationRuntime=restoration)
        for queue in self.plan["queues"]:
            current = policy_from(queue_attrs(self.aws, queue))
            restored = restored_policy(current, queue["original"], self.sid, queue["arn"])
            if restored == current:
                continue
            self.checked_mutation({"op": "restore-queue", "name": queue["name"], "expectedPolicy": restored},
                lambda queue=queue, current=current, restored=restored: self.write_policy(queue, current, restored),
                lambda queue=queue, restored=restored: require(policy_from(queue_attrs(self.aws, queue)) == restored, "queue-restore-unconfirmed"))
        for priority, arn in self.owned_rules().items():
            self.checked_mutation({"op": "delete-rule", "priority": priority, "arn": arn},
                lambda arn=arn: self.aws("elbv2", "delete-rule", rule_arn=arn),
                lambda priority=priority: require(priority not in self.owned_rules(), "rule-delete-unconfirmed"))
        self.verify_retirement(previous=restoration == "previous")
        self.verify_restored()
        self.save(phase="restored", intent=None)
        self.lock.clear_window(self.table, self.owner, expected=self.state)
        self.state = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "open", "drain", "close", "inspect-clear"))
    parser.add_argument("--plan", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--table", default=TABLE, choices=(TABLE,))
    parser.add_argument("--candidate")
    parser.add_argument("--previous")
    parser.add_argument("--transition-anchor", default=ANCHOR, choices=(ANCHOR,))
    args = parser.parse_args()
    try:
        binding = ci_binding(args.owner, args.source)
        aws = Aws()
        if args.action == "plan":
            require(args.candidate and args.previous, "manifest-paths-required")
            require(deployment_lock.load_window(args.table, args.owner) is None, "existing-window-requires-recovery")
            plan = make_plan(aws, binding, json.loads(Path(args.candidate).read_text(encoding="utf-8")),
                             json.loads(Path(args.previous).read_text(encoding="utf-8")), args.transition_anchor)
            Path(args.plan).write_text(canonical(plan) + "\n", encoding="utf-8")
            state = None
        else:
            verify_aws(aws)
            plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            window = Window(aws, deployment_lock, args.table, plan, binding)
            if args.action == "inspect-clear":
                window.close(inspect_only=True)
            else:
                getattr(window, args.action)()
            state = window.state
        receipt = {"required": plan["required"], "planId": plan["id"], "action": args.action,
                   "phase": state["phase"] if state else "clear", **binding}
        if args.action in ("close", "inspect-clear"):
            receipt["restorationRuntime"] = window.restoration_runtime or ("already-clear" if plan["required"] else "not-required")
        print(canonical(receipt))
        return 0
    except (CutoverError, OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired) as error:
        # deployment_lock may carry raw AWS output; do not forward its exception.
        print(canonical({"ok": False, "code": str(error) if isinstance(error, CutoverError) else "cutover-unconfirmed"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
