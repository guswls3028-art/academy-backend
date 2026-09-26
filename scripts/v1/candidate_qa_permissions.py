"""Render exact per-lease runtime permissions; does not apply IAM or read keys.

IAM transaction permissions use the underlying Get/Put/Update/ConditionCheck
actions: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis-iam.html
The caller must verify sole inert QA capacity, role/profile inventory and the
shared lock before an exact-policy write. The initial foundation policy remains
unable to read signing keys or change lease control records.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import re

from candidate_qa_window import require

ACCOUNT = "809466760795"
REGION = "ap-northeast-2"
TABLE = f"arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/academy-v1-video-job-lock"
ROLE = "academy-api-qa-role"
POLICY = "academy-candidate-runtime"


def fingerprint(policy):
    return hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def runtime_policy(lease_id, *, base=None):
    require(isinstance(lease_id,str) and re.fullmatch(r"[0-9a-f]{32}",lease_id),
            "Exact random lease identifier required")
    trusted=json.loads((Path(__file__).parent/"templates/iam/policy_candidate_qa_runtime.json").read_text())
    require(base is None or base==trusted,"Runtime policy drift must be reconciled before activation")
    result=copy.deepcopy(trusted)
    key=f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/academy/qa-leases/{lease_id}/message-signing-key"
    denies=[s for s in result["Statement"] if s.get("Sid")=="DenyOtherParameters"]
    require(len(denies)==1,"Exact existing parameter boundary required")
    denies[0]["NotResource"].append(key)
    result["Statement"].extend([
        {"Sid":"BoundQaSigningKey","Effect":"Allow","Action":"ssm:GetParameter","Resource":key},
        {"Sid":"ReadCommittedQaControl","Effect":"Allow","Action":"dynamodb:GetItem","Resource":TABLE,
         "Condition":{"ForAllValues:StringEquals":{"dynamodb:LeadingKeys":[
             "__candidate_qa_window__","__deployment_control_v2__"]},
             "Null":{"dynamodb:LeadingKeys":"false"}}},
        {"Sid":"CheckCommittedQaLease","Effect":"Allow","Action":"dynamodb:ConditionCheckItem","Resource":TABLE,
         "Condition":{"ForAllValues:StringEquals":{"dynamodb:LeadingKeys":["__candidate_qa_window__"]},
             "ForAnyValue:StringEquals":{"dynamodb:EnclosingOperation":["TransactWriteItems"]},
             "Null":{"dynamodb:LeadingKeys":"false"}}},
        {"Sid":"OwnQaMessageJournal","Effect":"Allow",
         "Action":["dynamodb:GetItem","dynamodb:PutItem","dynamodb:UpdateItem"],"Resource":TABLE,
         "Condition":{"ForAllValues:StringLike":{"dynamodb:LeadingKeys":[
             "__candidate_qa_message__:"+lease_id+":*"]},
             "Null":{"dynamodb:LeadingKeys":"false"}}},
    ])
    return result


def activation_plan(lease_id, current):
    proposed=runtime_policy(lease_id,base=current)
    return {"mode":"plan-only","role":ROLE,"policy":POLICY,"lease_id":lease_id,
            "before_sha256":fingerprint(current),"after_sha256":fingerprint(proposed),
            "proposed_policy":proposed,
            "required_before_apply":["clean exact main controller","owned live shared lock",
                "sole exact inert QA instance/profile","full role policy inventory unchanged",
                "reviewed exact policy fingerprints","principal IAM positive/negative simulations"],
            "applied":False}
