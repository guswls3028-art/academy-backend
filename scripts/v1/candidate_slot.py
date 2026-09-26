"""Read-only development slot guard and nonsecret rollback coordinates."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ACCOUNT = "809466760795"
REGION = "ap-northeast-2"
REGISTRY = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"
NAME = "academy-v1-api-development"
MANAGED = "academy-api-development"
ROLES = {"ApiImageUri": "api", "ToolsImageUri": "tools-worker",
         "AiImageUri": "ai-worker-cpu", "MessagingImageUri": "messaging-worker"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def aws(service, *args):
    result = subprocess.run(["aws", service, *args, "--region", REGION, "--output", "json"],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout or "{}")


def instances():
    result = aws("ec2", "describe-instances", "--filters",
                 "Name=tag:Name,Values=" + NAME, "Name=tag:ManagedBy,Values=" + MANAGED,
                 "Name=instance-state-name,Values=pending,running,stopping,stopped")
    require(not result.get("NextToken"), "Incomplete development instance inventory")
    return [v for r in result.get("Reservations", []) for v in r.get("Instances", [])]


def tags(instance):
    return {v["Key"]: v["Value"] for v in instance.get("Tags", [])}


def guard(owner="", require_baseline=False):
    """Never stop a session, drain queues or take another owner's slot."""
    if owner:
        require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9:._-]{2,120}", owner), "Invalid slot owner")
    current = instances()
    require(len(current) <= 1, "Multiple or incomplete development capacities need owner review")
    if require_baseline:
        require(len(current) == 1, "An exact development baseline is required")
    for instance in current:
        t = tags(instance)
        require(instance["State"]["Name"] == "running" and t.get("Lifecycle") == "active",
                "Development slot is not an idle active baseline")
        lease = t.get("SlotLeaseOwner", "")
        require(not lease or (owner and lease == owner), "Development slot belongs to another owner")
        sessions = aws("ssm", "describe-sessions", "--state", "Active", "--filters",
                       json.dumps([{"key": "Target", "value": instance["InstanceId"]}]))
        require(not sessions.get("Sessions") and not sessions.get("NextToken"), "Active SSM session owns the development slot")
    for queue in ("ai", "tools", "messaging"):
        name = f"academy-v1-development-{queue}-queue"
        url = aws("sqs", "get-queue-url", "--queue-name", name)["QueueUrl"]
        require(url == f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/{name}", "Queue boundary differs")
        response = aws("sqs", "get-queue-attributes", "--queue-url", url, "--attribute-names",
                       "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible",
                       "ApproximateNumberOfMessagesDelayed")
        attrs = response.get("Attributes", {})
        require(set(attrs) >= {"ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible",
                               "ApproximateNumberOfMessagesDelayed"}, "Queue inventory is incomplete")
        require(all(int(attrs[k]) == 0 for k in ("ApproximateNumberOfMessages",
                    "ApproximateNumberOfMessagesNotVisible", "ApproximateNumberOfMessagesDelayed")),
                "Development queues are busy")
    return current


def validate_snapshot(snapshot):
    require(snapshot.get("schemaVersion") == 1 and snapshot.get("capacity") == 1,
            "Invalid baseline snapshot")
    require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9:._-]{2,120}", snapshot.get("owner", "")),
            "Snapshot owner is missing")
    require(snapshot.get("profile") == f"arn:aws:iam::{ACCOUNT}:instance-profile/academy-api-development",
            "Baseline must use the normal development profile")
    require(re.fullmatch(r"sha-[0-9a-f]{40}-run-[1-9][0-9]*-[1-9][0-9]*", snapshot.get("release", "")),
            "Baseline release is not immutable")
    for key, role in ROLES.items():
        require(re.fullmatch(re.escape(REGISTRY + "/academy-" + role + "@sha256:") + "[0-9a-f]{64}",
                             snapshot.get("images", {}).get(key, "")), "Baseline image identity differs")
    for key in ("api_version", "workers_version"):
        require(type(snapshot.get(key)) is int and snapshot[key] > 0, "Baseline environment version missing")
    require(re.fullmatch(r"[a-z][a-z0-9_]{2,62}", snapshot.get("production_database", "")),
            "Baseline production denial target missing")
    return snapshot


def capture(owner):
    require(bool(owner), "A named QA slot owner is required")
    current = guard(owner, require_baseline=True)
    instance = current[0]; t = tags(instance)
    require(not t.get("SlotLeaseOwner"), "Nested QA cannot replace another QA baseline")
    snapshot = {
        "schemaVersion": 1, "owner": owner, "capacity": 1, "instance_id": instance["InstanceId"],
        "profile": instance.get("IamInstanceProfile", {}).get("Arn"),
        "ami": instance["ImageId"], "instance_type": instance["InstanceType"],
        "release": t.get("ReleaseId"), "images": {k:t.get(k) for k in ROLES},
        "api_version": int(t.get("ApiEnvVersion", "0")),
        "workers_version": int(t.get("WorkersEnvVersion", "0")),
        "production_database": t.get("ProductionDatabase"),
    }
    return validate_snapshot(snapshot)


def load_snapshot(path, digest):
    raw = path.read_bytes()
    require(re.fullmatch(r"[0-9a-f]{64}", digest) and hashlib.sha256(raw).hexdigest() == digest,
            "Baseline snapshot hash differs")
    return validate_snapshot(json.loads(raw))


def assess(snapshot, require_restored=False):
    current = guard(snapshot["owner"], require_baseline=True)
    instance = current[0]; t = tags(instance)
    exact = (instance.get("IamInstanceProfile", {}).get("Arn") == snapshot["profile"]
             and t.get("ReleaseId") == snapshot["release"]
             and t.get("ApiEnvVersion") == str(snapshot["api_version"])
             and t.get("WorkersEnvVersion") == str(snapshot["workers_version"])
             and all(t.get(k) == v for k,v in snapshot["images"].items())
             and not t.get("SlotLeaseOwner") and not t.get("QaMode"))
    if not exact:
        require(t.get("SlotLeaseOwner") == snapshot["owner"] and t.get("QaMode") == "isolated-qa",
                "A foreign baseline replaced this QA; restoration refused")
    if require_restored:
        require(exact, "Baseline restoration readback differs")
        leftovers = aws("ec2", "describe-instances", "--filters",
                        "Name=tag:SlotLeaseOwner,Values=" + snapshot["owner"],
                        "Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down")
        require(not leftovers.get("NextToken") and not any(r.get("Instances") for r in leftovers.get("Reservations", [])),
                "Owned temporary QA capacity remains")
    return {"replace": not exact, "instance_id": instance["InstanceId"], "restored": exact}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("command",choices=("guard","capture","assess","verify"))
    p.add_argument("--owner",default="")
    p.add_argument("--snapshot",type=Path)
    p.add_argument("--sha256")
    p.add_argument("--github-output",type=Path)
    a=p.parse_args()
    try:
        if a.command=="guard":
            guard(a.owner); result={"slot":"idle"}
        elif a.command=="capture":
            require(a.snapshot is not None,"Snapshot output required")
            # Capture only under the mutation lock, after the pre-lock idle check.
            subprocess.run(["python3",str(Path(__file__).with_name("deployment_lock.py")),"assert-owned",
                            "--owner",os.environ["ACADEMY_DEPLOY_LOCK_OWNER"]],check=True)
            result=capture(a.owner)
            raw=(json.dumps(result,indent=2)+"\n").encode()
            a.snapshot.parent.mkdir(parents=True,exist_ok=True);a.snapshot.write_bytes(raw)
            if a.github_output:
                with a.github_output.open("a") as f:
                    f.write("sha256="+hashlib.sha256(raw).hexdigest()+"\n")
        else:
            require(a.snapshot is not None and a.sha256,"Exact snapshot coordinates required")
            result=assess(load_snapshot(a.snapshot,a.sha256),a.command=="verify")
        print(json.dumps(result,sort_keys=True))
    except Exception:
        p.exit(2,"CANDIDATE_SLOT_BLOCKED: owner, session, queue or immutable baseline verification failed\n")


if __name__=="__main__":
    main()
