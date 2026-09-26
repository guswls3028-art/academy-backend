"""Structural IAM contract tests only; actual IAM simulation is a live gate."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts/v1"))
from candidate_qa_permissions import runtime_policy, activation_plan
from scripts.v1.candidate_qa_window import WindowHold


class PermissionTests(unittest.TestCase):
    def base(self):
        return json.loads((Path(__file__).resolve().parents[1]/"scripts/v1/templates/iam/policy_candidate_qa_runtime.json").read_text())

    def test_exact_key_exception_preserves_parameter_and_production_denies(self):
        original=self.base();policy=runtime_policy("a"*32,base=original)
        by_sid={s["Sid"]:s for s in policy["Statement"]}
        before={s["Sid"]:s for s in original["Statement"]}
        self.assertEqual(by_sid["DenyPathAndHistory"],before["DenyPathAndHistory"])
        self.assertEqual(by_sid["DenyProductionRepositories"],before["DenyProductionRepositories"])
        self.assertEqual(by_sid["DenyOtherParameters"]["NotResource"][:-1],before["DenyOtherParameters"]["NotResource"])
        key=by_sid["BoundQaSigningKey"]["Resource"]
        self.assertEqual(key,by_sid["DenyOtherParameters"]["NotResource"][-1])
        self.assertNotIn("*",key)
        self.assertIn("/"+"a"*32+"/message-signing-key",key)
        self.assertEqual(original,self.base())

    def test_runtime_cannot_write_control_or_foreign_nonce_records(self):
        policy=runtime_policy("a"*32)
        ddb=[s for s in policy["Statement"] if str(s["Action"]).find("dynamodb:")>=0]
        for statement in ddb:
            actions=statement["Action"] if isinstance(statement["Action"],list) else [statement["Action"]]
            self.assertNotIn("dynamodb:DeleteItem",actions)
            self.assertNotIn("dynamodb:Scan",actions)
            self.assertEqual(statement["Condition"]["Null"]["dynamodb:LeadingKeys"],"false")
            if any(a in actions for a in ("dynamodb:PutItem","dynamodb:UpdateItem")):
                self.assertEqual(statement["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"],
                                 ["__candidate_qa_message__:"+"a"*32+":*"])
            self.assertFalse(any(a in actions for a in ("dynamodb:TransactGetItems","dynamodb:TransactWriteItems")))

    def test_foreign_policy_or_ambiguous_lease_cannot_be_planned(self):
        for value in ("*", "a"*31, "a"*32+":foreign", None):
            with self.assertRaises(WindowHold):runtime_policy(value)
        base=self.base();base["Statement"].append({"Effect":"Allow","Action":"*","Resource":"*"})
        with self.assertRaises(WindowHold):activation_plan("a"*32,base)

    def test_plan_contains_exact_policy_fingerprints_and_no_apply(self):
        plan=activation_plan("a"*32,self.base())
        self.assertFalse(plan["applied"])
        self.assertNotEqual(plan["before_sha256"],plan["after_sha256"])
        self.assertEqual(len(plan["after_sha256"]),64)
        self.assertEqual(plan["role"],"academy-api-qa-role")

    def test_controller_can_pass_only_qa_role_and_write_only_lease_control(self):
        root=Path(__file__).resolve().parents[1]/"scripts/v1/templates/iam"
        policy=json.loads((root/"policy_candidate_production.json").read_text())
        statements={s["Sid"]:s for s in policy["Statement"]}
        self.assertEqual(statements["QaPreparePassRole"]["Resource"],
                         "arn:aws:iam::809466760795:role/academy-api-qa-role")
        control=statements["CandidateQaLeaseControl"]
        self.assertEqual(control["Condition"]["ForAllValues:StringEquals"]["dynamodb:LeadingKeys"],
                         ["__candidate_qa_window__"])
        self.assertNotIn("dynamodb:DeleteItem",control["Action"])
        self.assertIn("iam:PutRolePolicy",statements["DenyProductionControl"]["Action"])
