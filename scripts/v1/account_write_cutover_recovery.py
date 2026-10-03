#!/usr/bin/env python3
"""Manual recovery of one approved, failed CI account window; plan is read-only.

No CI environment or lock owner is adopted. A same-row CAS proof preserves the
actual window's complete oldFleet evidence across close/lock-release failures.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import time

if __package__:
    from . import account_write_cutover as core
    from . import deployment_lock
else:
    import account_write_cutover as core
    import deployment_lock

REPOSITORY = "guswls3028-art/academy-backend"
WORKFLOW = ".github/workflows/v1-build-and-push-latest.yml"
MARKER = "accountRecovery"
REPO_ROOT = Path(__file__).resolve().parents[2]


class RecoveryError(RuntimeError):
    """Fixed diagnostic only; provider output never reaches the receipt."""


def require(ok, code):
    if not ok:
        raise RecoveryError(code)


def command(argv, code, *, json_output=False, timeout=120):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        require(result.returncode == 0, code)
        return json.loads(result.stdout or "{}") if json_output else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise RecoveryError(code) from None


def assert_fresh_source(source):
    command(["pwsh", "-NoProfile", "-File",
             str(REPO_ROOT / "scripts/v1/assert-production-source-freshness.ps1"),
             "-RepoRoot", str(REPO_ROOT)], "recovery-source-not-fresh")
    command(["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", source, "HEAD"],
            "recovery-original-source-not-ancestor")


def gh_api(path):
    return command(["gh", "api", "--hostname", "github.com", "--method", "GET", path],
                   "recovery-github-unconfirmed", json_output=True)


class Aws:
    def __call__(self, service, operation, _timeout=60, **args):
        require(isinstance(_timeout, (int, float)) and 0 < _timeout <= 120,
                "recovery-aws-deadline-invalid")
        argv = ["aws", service, operation, "--region", core.REGION,
                "--output", "json", "--no-cli-pager"]
        for key, value in args.items():
            if value is False:
                continue
            argv.append("--" + key.replace("_", "-"))
            if value is True:
                continue
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                argv.extend(value)
            elif isinstance(value, (dict, list)):
                argv.append(core.canonical(value))
            else:
                argv.append(str(value))
        try:
            result = command(argv, "recovery-aws-unconfirmed", json_output=True, timeout=_timeout)
            require(isinstance(result, dict), "recovery-aws-unconfirmed")
        except RecoveryError:
            # Preserve Window.checked_mutation's uncertain-write/readback contract.
            raise core.CutoverError("aws-call-unconfirmed") from None
        return result


def binding_for(plan, owner, source):
    match = re.fullmatch(r"ci-deploy:([1-9][0-9]*):([1-9][0-9]*)", owner)
    require(match is not None and re.fullmatch(r"[0-9a-f]{40}", source), "recovery-binding-invalid")
    binding = {"owner": owner, "source": source,
               "runId": match[1], "runAttempt": match[2]}
    core.validate_plan(plan, binding)
    require(plan["required"] is True and plan["candidate"]["gitSha"] == source,
            "recovery-plan-not-required")
    return binding


def verify_failed_run(gh, binding):
    path = f"repos/{REPOSITORY}/actions/runs/{binding['runId']}"
    for value in (gh(path), gh(f"{path}/attempts/{binding['runAttempt']}")):
        require(isinstance(value, dict) and value.get("id") == int(binding["runId"])
                and value.get("run_attempt") == int(binding["runAttempt"])
                and value.get("head_sha") == binding["source"]
                and value.get("repository", {}).get("full_name") == REPOSITORY
                and value.get("head_repository", {}).get("full_name") == REPOSITORY
                and value.get("path", "").split("@", 1)[0] == WORKFLOW
                and value.get("head_branch") == "main"
                and value.get("event") in ("push", "workflow_dispatch"), "recovery-run-mismatch")
        require(value.get("status") == "completed"
                and value.get("conclusion") in ("failure", "cancelled"), "recovery-run-not-terminal")
    # GitHub's review-history endpoint is run-scoped, not attempt-scoped. Do not
    # attribute an old approval to a later attempt without platform evidence.
    require(binding["runAttempt"] == "1", "recovery-approval-attempt-unprovable")
    approvals = gh(f"{path}/approvals")
    require(isinstance(approvals, list) and any(
        isinstance(review, dict) and review.get("state") == "approved"
        and isinstance(review.get("user"), dict) and type(review["user"].get("id")) is int
        and review["user"]["id"] > 0
        and any(isinstance(env, dict) and env.get("name") == "production"
                and type(env.get("id")) is int and env["id"] > 0
                for env in review.get("environments", [])) for review in approvals),
        "recovery-production-approval-missing")


class Recovery:
    def __init__(self, aws, *, gh=gh_api, freshness=assert_fresh_source,
                 lock=deployment_lock, observer=core.observe, clock=time.time):
        self.aws, self.gh, self.freshness = aws, gh, freshness
        self.lock, self.observer, self.clock = lock, observer, clock

    def guards(self, binding):
        self.freshness(binding["source"])
        verify_failed_run(self.gh, binding)
        identity = self.aws("sts", "get-caller-identity")
        arn = identity.get("Arn", "")
        require(identity.get("Account") == core.ACCOUNT and (
            arn == f"arn:aws:iam::{core.ACCOUNT}:root" or
            re.fullmatch(r"arn:aws:sts::" + core.ACCOUNT
                         + r":assumed-role/academy-gha-ecr-build/[A-Za-z0-9_+=,.@-]+", arn)),
            "recovery-manual-principal-unapproved")

    def item(self):
        return self.aws("dynamodb", "get-item", table_name=core.TABLE,
                        key={"videoId": {"S": deployment_lock.LOCK_KEY}},
                        consistent_read=True).get("Item", {})

    def owned(self, item, binding):
        require(item.get("owner") == {"S": binding["owner"]}
                and int(item.get("ttl", {}).get("N", "0")) > int(self.clock()),
                "recovery-lock-owner-mismatch")

    def proof(self, plan, binding, state):
        require(type(state.get("startedAt")) is int and state["startedAt"] > 0,
                "recovery-window-evidence-invalid")
        old = state.get("oldFleet")
        require(isinstance(old, dict) and set(old) == set(core.COMPONENTS),
                "recovery-window-evidence-invalid")
        for repo, instances in old.items():
            require(isinstance(instances, list) and instances
                    and all(isinstance(i, str) and re.fullmatch(r"i-[0-9a-f]{8,17}", i) for i in instances)
                    and set(plan["oldFleet"][repo]).issubset(instances),
                    "recovery-window-evidence-invalid")
        return {"version": 1, "binding": binding, "planId": plan["id"],
                "startedAt": state["startedAt"], "oldFleet": copy.deepcopy(old)}

    def marker(self, item, plan, binding):
        raw = item.get(MARKER)
        if raw is None:
            return None
        require(isinstance(raw, dict) and set(raw) == {"S"}, "recovery-proof-mismatch")
        value = json.loads(raw["S"])
        require(isinstance(value, dict) and value.get("version") == 1
                and value.get("binding") == binding and value.get("planId") == plan["id"]
                and value == self.proof(plan, binding, value), "recovery-proof-mismatch")
        return value

    def inspection(self, plan, binding, proof):
        window = core.Window(self.aws, self.lock, core.TABLE, plan, binding, self.observer)
        # Read-only inspection context: all historical fleet evidence is copied
        # from the actual original window's CAS proof, never rebuilt from plan.
        # This context is never passed to close() or another mutating method.
        window.state = copy.deepcopy(proof)
        runtime = self.runtime(window)
        window.verify_restored()
        return runtime

    @staticmethod
    def runtime(window):
        try:
            window.verify_retirement()
            return "candidate"
        except core.CutoverError:
            window.verify_retirement(previous=True)
            return "previous"

    def run(self, plan, owner, source, *, apply=False):
        binding = binding_for(plan, owner, source)
        self.guards(binding)
        item = self.item()
        self.owned(item, binding)
        window = core.Window(self.aws, self.lock, core.TABLE, plan, binding, self.observer)
        state = window.load()
        marker = self.marker(item, plan, binding)
        if state is not None:
            require(item.get("accountWindowTtl") == {"N": str(deployment_lock.WINDOW_PIN_TTL)}
                    and item.get("ttl") == item["accountWindowTtl"]
                    and json.loads(item["accountWindow"]["S"]) == state, "recovery-window-pin-mismatch")
            expected = self.proof(plan, binding, state)
            require(marker is None or marker == expected, "recovery-proof-mismatch")
            marker = expected
        else:
            require("accountWindow" not in item and "accountWindowTtl" not in item
                    and marker is not None, "recovery-cleared-proof-missing")
        receipt = {"ok": True, "planId": plan["id"], **binding,
                   "action": "apply-close" if apply else "plan", "released": False}
        if not apply:
            runtime = self.runtime(window) if state is not None else self.inspection(plan, binding, marker)
            return {**receipt, "runtimeVerified": True, "restorationRuntime": runtime}
        if state is not None:
            self.aws("dynamodb", "update-item", table_name=core.TABLE,
                     key={"videoId": {"S": deployment_lock.LOCK_KEY}},
                     update_expression="SET #proof = :proof",
                     condition_expression="#owner = :owner AND #ttl = :pin AND #pin = :pin AND #window = :window AND (attribute_not_exists(#proof) OR #proof = :proof)",
                     expression_attribute_names={"#owner": "owner", "#ttl": "ttl", "#pin": "accountWindowTtl",
                                                 "#window": "accountWindow", "#proof": MARKER},
                     expression_attribute_values={":owner": {"S": owner}, ":pin": item["ttl"],
                                                  ":window": item["accountWindow"],
                                                  ":proof": {"S": core.canonical(marker)}})
        else:
            self.inspection(plan, binding, marker)
        window.close()
        window.close(inspect_only=True)
        # A fresh authentic check gates the final write too. Window.close cleared
        # the pin only after its own gates; the original proof survives that CAS.
        runtime = self.inspection(plan, binding, marker)
        self.guards(binding)
        final = self.item()
        self.owned(final, binding)
        require("accountWindow" not in final and "accountWindowTtl" not in final
                and self.marker(final, plan, binding) == marker, "recovery-release-not-restored")
        try:
            self.aws("dynamodb", "delete-item", table_name=core.TABLE,
                     key={"videoId": {"S": deployment_lock.LOCK_KEY}},
                     condition_expression="#owner = :owner AND #ttl > :now AND attribute_not_exists(#window) AND attribute_not_exists(#pin) AND #proof = :proof",
                     expression_attribute_names={"#owner": "owner", "#ttl": "ttl", "#window": "accountWindow",
                                                 "#pin": "accountWindowTtl", "#proof": MARKER},
                     expression_attribute_values={":owner": {"S": owner}, ":now": {"N": str(int(self.clock()))},
                                                  ":proof": {"S": core.canonical(marker)}})
        except core.CutoverError:
            # A lost delete acknowledgement is success only after actual absence;
            # any live/foreign row is retained and the original failure survives.
            if self.item():
                raise
        require(not self.item(), "recovery-release-unconfirmed")
        return {**receipt, "runtimeVerified": True, "restorationRuntime": runtime, "released": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="plan", choices=("plan", "apply-close"))
    parser.add_argument("--plan", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args(argv)
    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        print(core.canonical(Recovery(Aws()).run(plan, args.owner, args.source,
                                                apply=args.action == "apply-close")))
        return 0
    except (RecoveryError, core.CutoverError, RuntimeError, OSError, ValueError,
            KeyError, TypeError, subprocess.TimeoutExpired) as error:
        code = str(error) if isinstance(error, (RecoveryError, core.CutoverError)) else "recovery-unconfirmed"
        if not re.fullmatch(r"[a-z][a-z0-9-]+", code):
            code = "recovery-unconfirmed"
        print(core.canonical({"ok": False, "code": code}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
