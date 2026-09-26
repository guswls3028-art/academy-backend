"""Offline QA admission/queue provenance tests; no provider or AWS calls."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from apps.infrastructure.qa_lease import (
    AdmissionGate, MessageClaims, QaLeaseClosed, QaMessageCompleted, QaMessageInFlight,
    binding_sha256,
)


class FakeClaims:
    def __init__(self):
        self.rows={}
    def claim(self,stamp):
        key=stamp["message_id"]
        identity=hashlib.sha256(json.dumps(stamp,sort_keys=True).encode()).hexdigest()
        row=self.rows.get(key)
        if row:
            if row["identity"] != identity: raise QaLeaseClosed("different identity")
            if row["state"]=="completed": raise QaMessageCompleted("completed")
            if row["state"]=="inflight": raise QaMessageInFlight("inflight")
        claim={"key":key,"identity":identity,"state":"inflight"}
        self.rows[key]=claim
        return claim
    def finish(self,claim,completed):
        claim["state"]="completed" if completed else "retryable"


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now=1000
        self.record={
            "lease_id":"a"*32,"owner_task":"01a0d04a-d64f-7473-9f58-61a8e983dcc0",
            "lock_owner":"candidate:123:1","source_sha":"b"*40,
            "images":{k:"sha256:"+"c"*64 for k in ("api","ai","tools","messaging")},
            "endpoint":"ssm://i-0123456789abcdef0:8001",
            "profile":"arn:aws:iam::809466760795:instance-profile/academy-api-qa",
            "scope":[509,511],"baseline_sha256":"d"*64,"tenant_ids":[101],
            "message_key_version":1,"state":"active","revision":1,
            "started_at":900,"renewed_at":900,"expires_at":1600,
        }
        self.context={
            "ACADEMY_RUNTIME_ENV":"development","ACADEMY_QA_MODE":"isolated-qa",
            "ACADEMY_QA_LEASE_ID":"a"*32,"ACADEMY_QA_BINDING_SHA256":binding_sha256(self.record),
            "ACADEMY_QA_MESSAGE_KEY_VERSION":"1","ACADEMY_QA_MESSAGE_SIGNING_KEY":"e"*64,
        }
        self.claims=FakeClaims()
        self.gates=[]

    def gate(self,kind="ai",**kwargs):
        gate=AdmissionGate(kind,context=self.context,
            reader=lambda:(copy.deepcopy(self.record),{"owner":"candidate:123:1","expires_at":2000}),
            clock=lambda:self.now,activity_dir=self.temp.name,heartbeat=False,
            claims=self.claims,**kwargs)
        self.gates.append(gate)
        self.addCleanup(gate.close)
        return gate

    def payload(self,gate=None):
        return (gate or self.gate()).stamp_message({"question":"synthetic","_qa_lease":{"untrusted":True}},
                                                  101,job_id="job-123")

    def test_default_runtime_never_reads_or_tracks(self):
        reader=Mock(side_effect=AssertionError("AWS must not be used"))
        gate=AdmissionGate("messaging",context={},reader=reader)
        self.assertIsNone(gate.admit())
        with gate.begin("ordinary-production-job"):pass
        self.assertEqual(gate.snapshot(),{"enabled":False,"kind":"messaging"})
        reader.assert_not_called()

    def test_partial_qa_configuration_and_production_qa_flag_fail(self):
        for context in ({"ACADEMY_QA_LEASE_ID":"a"*32},
                        dict(self.context,ACADEMY_RUNTIME_ENV="production"),
                        dict(self.context,ACADEMY_QA_MODE="unknown")):
            with self.assertRaises(QaLeaseClosed):AdmissionGate("api",context=context)

    def test_producer_overwrites_untrusted_stamp_and_checks_authoritative_tenant(self):
        gate=self.gate();payload=self.payload(gate)
        stamp=gate.validate_message(payload,tenant_id=101,job_id="job-123")
        self.assertNotIn("untrusted",stamp)
        self.assertEqual(stamp["tenant_id"],101)
        self.assertEqual(stamp["queue_kind"],"ai")
        for tenant in (102,None,True,101.2):
            with self.assertRaises(QaLeaseClosed):gate.stamp_message({},tenant,job_id="job-123")

    def test_cross_tenant_wrong_job_and_wrong_worker_rejected(self):
        payload=self.payload()
        for tenant,job in ((102,"job-123"),(None,"job-123"),(101,"other-job")):
            with self.assertRaises(QaLeaseClosed):
                self.gate().validate_message(payload,tenant_id=tenant,job_id=job)
        with self.assertRaises(QaLeaseClosed):
            self.gate("tools").validate_message(payload,tenant_id=101,job_id="job-123")

    def test_signature_body_and_stamp_tampering_fail(self):
        for field,value in (("question","changed"),("_qa_lease",{})):
            payload=self.payload();payload[field]=value
            with self.assertRaises(QaLeaseClosed):
                self.gate().validate_message(payload,tenant_id=101,job_id="job-123")
        for field,value in (("tenant_id",102),("lease_id","f"*32),("revision",2),
                            ("source_sha","f"*40),("issued_at",800)):
            payload=self.payload();payload["_qa_lease"][field]=value
            with self.assertRaises(QaLeaseClosed):
                self.gate().validate_message(payload,tenant_id=101,job_id="job-123")

    def test_revision_and_digest_change_reject_old_receipt(self):
        payload=self.payload();self.record["revision"]=2
        with self.assertRaises(QaLeaseClosed):
            self.gate().validate_message(payload,tenant_id=101,job_id="job-123")
        self.record["revision"]=1;self.record["images"]["ai"]="sha256:"+"f"*64
        with self.assertRaises(QaLeaseClosed):self.gate().admit()

    def test_clock_expiry_and_rotation_fail_closed(self):
        gate=self.gate();payload=self.payload(gate)
        self.now=999
        with self.assertRaises(QaLeaseClosed):gate.validate_message(payload,tenant_id=101,job_id="job-123")
        self.now=1570
        with self.assertRaises(QaLeaseClosed):gate.admit()
        self.now=1000
        gate.context["ACADEMY_QA_MESSAGE_KEY_VERSION"]="2"
        with self.assertRaises(QaLeaseClosed):gate.validate_message(payload,tenant_id=101,job_id="job-123")
        gate.context["ACADEMY_QA_MESSAGE_KEY_VERSION"]="1"
        gate.context["ACADEMY_QA_MESSAGE_SIGNING_KEY"]="invalid"
        with self.assertRaises(QaLeaseClosed):gate.stamp_message({},101,job_id="job-123")

    def test_unavailable_lease_and_missing_key_fail_before_processing(self):
        gate=self.gate();gate._reader=Mock(side_effect=RuntimeError("service unavailable"))
        with self.assertRaises(QaLeaseClosed):gate.admit()
        self.assertFalse(gate.snapshot()["lease_verified"])
        gate=self.gate();gate._key=Mock(side_effect=QaLeaseClosed("key unavailable"))
        with self.assertRaises(QaLeaseClosed):gate.stamp_message({},101,job_id="job-123")

    def test_receipt_hash_identity_and_expiry_do_not_interrupt_existing_work(self):
        gate=self.gate();payload=self.payload(gate)
        with gate.begin("job-123:receipt-one",tenant_id=101,message=payload,job_id="job-123"):
            item=gate.snapshot()["inflight"][0]
            self.assertEqual(item["operation_sha256"],hashlib.sha256(b"job-123:receipt-one").hexdigest())
            self.assertEqual(item["tenant_id"],101)
            self.assertEqual(item["message_id"],payload["_qa_lease"]["message_id"])
            self.now=1601
            with self.assertRaises(QaLeaseClosed):gate.admit()
            self.assertEqual(len(gate.snapshot()["inflight"]),1)
        self.assertFalse(gate.snapshot()["inflight"])
        text="".join(p.read_text() for p in Path(self.temp.name).glob("*.json"))
        self.assertNotIn("receipt-one",text)
        self.assertNotIn(self.context["ACADEMY_QA_MESSAGE_SIGNING_KEY"],text)

    def test_same_message_retry_inflight_and_completed_dispositions(self):
        gate=self.gate();other=self.gate();payload=self.payload(gate)
        with gate.begin("receipt1",tenant_id=101,message=payload,job_id="job-123"):
            with self.assertRaises(QaMessageInFlight):
                with other.begin("receipt2",tenant_id=101,message=payload,job_id="job-123"):pass
            # No explicit completion: callback retry remains legal.
        with other.begin("receipt3",tenant_id=101,message=payload,job_id="job-123"):
            other.complete_message(payload)
        with self.assertRaises(QaMessageCompleted):
            with gate.begin("receipt4",tenant_id=101,message=payload,job_id="job-123"):pass
        self.assertFalse(gate.snapshot()["holds"])

    def test_rejection_is_identifiable_hold_and_not_queue_delete(self):
        gate=self.gate();payload=self.payload(gate)
        gate.hold_message(payload,reason="sensitive exception body must not be copied")
        self.assertEqual(gate.snapshot()["holds"][0]["message_id"],payload["_qa_lease"]["message_id"])
        self.assertNotIn("sensitive",json.dumps(gate.snapshot()))
        with self.assertRaises(QaLeaseClosed):gate.admit()

    def test_auth_purpose_is_api_only_and_never_grants_enqueue_tenant(self):
        gate=self.gate("api")
        with gate.begin("auth-bootstrap",purpose="auth"):
            self.assertIsNone(gate.snapshot()["inflight"][0]["tenant_id"])
        with self.assertRaises(QaLeaseClosed):
            with gate.begin("regular-api-write"):pass
        with self.assertRaises(QaLeaseClosed):
            with self.gate("ai").begin("worker-auth",purpose="auth"):pass
        with self.assertRaises(QaLeaseClosed):
            gate.stamp_message({},None,job_id="job",queue_kind="ai")


class ClaimJournalTests(unittest.TestCase):
    def stamp(self):
        return {"lease_id":"a"*32,"message_id":"b"*32,"revision":1,"payload_sha256":"c"*64,"tenant_id":101}

    def test_claim_is_atomic_with_current_lease_revision(self):
        client=Mock();client.get_item.return_value={}
        claim=MessageClaims(client).claim(self.stamp())
        tx=client.transact_write_items.call_args.kwargs["TransactItems"]
        self.assertEqual(tx[0]["ConditionCheck"]["ExpressionAttributeValues"][":revision"],{"N":"1"})
        self.assertIn("attribute_not_exists",tx[1]["Put"]["ConditionExpression"])
        MessageClaims(client).finish(claim,False)
        self.assertEqual(client.update_item.call_args.kwargs["ExpressionAttributeValues"][":state"],{"S":"retryable"})

    def test_conditional_race_resolves_known_other_inflight(self):
        client=Mock()
        identity=hashlib.sha256(json.dumps(self.stamp(),sort_keys=True,separators=(",",":")).encode()).hexdigest()
        client.get_item.side_effect=[{},{"Item":{"identity":{"S":identity},"state":{"S":"inflight"},
                                                "claimOwner":{"S":"other"},"revision":{"N":"1"}}}]
        client.transact_write_items.side_effect=RuntimeError("conditional failure")
        with self.assertRaises(QaMessageInFlight):MessageClaims(client).claim(self.stamp())
        self.assertEqual(client.transact_write_items.call_count,1)

    def test_uncertain_claim_and_conflicting_identity_hold(self):
        client=Mock();client.get_item.side_effect=[{},RuntimeError("readback unavailable")]
        client.transact_write_items.side_effect=RuntimeError("lost acknowledgement")
        with self.assertRaises(QaLeaseClosed):MessageClaims(client).claim(self.stamp())
        client=Mock();client.get_item.return_value={"Item":{"identity":{"S":"different"}}}
        with self.assertRaises(QaLeaseClosed):MessageClaims(client).claim(self.stamp())
        client.transact_write_items.assert_not_called()
