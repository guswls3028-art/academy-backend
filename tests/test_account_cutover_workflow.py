"""Keep admission recovery in the same approved, locked release DAG."""
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _jobs():
    return yaml.safe_load((ROOT / ".github/workflows/v1-build-and-push-latest.yml").read_text(encoding="utf-8"))["jobs"]


def test_preprod_and_recorded_environment_review_precede_window_open():
    job = _jobs()["run-migrations"]
    assert job["environment"] == "production"
    assert "verify-api-preprod" in job["needs"]
    steps = job["steps"]
    positions = {}
    for index, step in enumerate(steps):
        run = step.get("run", "")
        for marker in ("deployment_lock.py renew", "account_write_cutover.py plan",
                       "account_write_cutover.py open", "account_write_cutover.py drain"):
            if marker in run:
                positions[marker] = index
        if step.get("with", {}).get("name", "").startswith("account-cutover-plan-"):
            positions["saved-plan"] = index
        if step.get("id") == "migrate":
            positions["migrate"] = index
    assert list(positions.values()) == sorted(positions.values())
    assert len(positions) == 6
    plan = steps[positions["account_write_cutover.py plan"]]["run"]
    assert "release_freshness.py" in plan
    assert "origin/main:docs/reports/release-manifest.latest.json" in plan
    assert '--source "$GITHUB_SHA"' in plan
    assert "--candidate docs/reports/release-manifest.candidate.json" in plan


def test_failed_open_still_has_close_path_after_all_affected_rollouts():
    jobs = _jobs()
    close = jobs["close-account-window"]
    assert set(close["needs"]) == {"run-migrations", "deploy-api", "deploy-ai", "deploy-tools"}
    assert close["if"].startswith("always()")
    assert "cutover_planned" in close["if"]
    assert ".result == 'success'" not in close["if"]
    commands = "\n".join(step.get("run", "") for step in close["steps"])
    assert "account_write_cutover.py close" in commands
    assert "account_write_cutover.py inspect-clear" in commands
    assert "close-account-window" in jobs["release-production-lock"]["needs"]
    verify = jobs["verify-deployment"]
    assert "close-account-window" in verify["needs"]
    assert "needs.close-account-window.result == 'success'" in verify["if"]
    assert "close-account-window" in jobs["notify-on-failure"]["needs"]


def test_cutover_policy_has_only_exact_queue_and_tagged_listener_mutations():
    document = json.loads((ROOT / "scripts/v1/templates/iam/policy_gha_account_cutover.json").read_text(encoding="utf-8"))
    statements = {entry["Sid"]: entry for entry in document["Statement"]}
    queue = statements["AccountCutoverQueuePolicy"]
    assert set(queue["Action"]) == {"sqs:GetQueueUrl", "sqs:GetQueueAttributes", "sqs:SetQueueAttributes"}
    assert set(queue["Resource"]) == {"__AI_QUEUE_ARN__", "__TOOLS_QUEUE_ARN__"}
    create = statements["AccountCutoverCreateRule"]
    assert create["Resource"] == "__LISTENER_ARN__"
    assert create["Condition"]["StringEquals"]["aws:RequestTag/AcademyCutover"] == "account-write-v1"
    delete = statements["AccountCutoverDeleteOwnRule"]
    assert delete["Resource"] == "__RULE_ARN_PREFIX__/*"
    assert delete["Condition"]["StringEquals"]["aws:ResourceTag/AcademyCutover"] == "account-write-v1"
    tagging = statements["AccountCutoverTagOnCreate"]
    assert tagging["Condition"]["StringEquals"]["elasticloadbalancing:CreateAction"] == "CreateRule"
    for statement in statements.values():
        actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        if statement["Resource"] == "*":
            assert all(action.startswith("elasticloadbalancing:Describe") for action in actions)
        assert not any(action in {"sqs:ReceiveMessage", "sqs:PurgeQueue", "elasticloadbalancing:ModifyRule",
                                  "elasticloadbalancing:ModifyListener", "iam:PutRolePolicy"} for action in actions)
