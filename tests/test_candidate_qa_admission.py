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
            "ACADEMY_RUNTIME_ENV":"development","ACADEMY_QA_MODE":"isolated-qa","CANDIDATE_LEASE_REQUIRED":"true",
            "ACADEMY_QA_LEASE_ID":"a"*32,"ACADEMY_QA_BINDING_SHA256":binding_sha256(self.record),
            "ACADEMY_QA_MESSAGE_KEY_VERSION":"1","ACADEMY_QA_MESSAGE_SIGNING_KEY":"e"*64,
        }
        self.metadata={"job_type":"synthetic","tier":"basic","source_domain":"qa","source_id":"one",
                       "created_at":"2026-09-26T00:00:00Z","attempt":0}
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
                                                  101,job_id="job-123",job_metadata=self.metadata)

    def test_default_runtime_never_reads_or_tracks(self):
        reader=Mock(side_effect=AssertionError("AWS must not be used"))
        gate=AdmissionGate("messaging",context={},reader=reader)
        self.assertIsNone(gate.admit())
        with gate.begin("ordinary-production-job"):pass
        self.assertEqual(gate.snapshot(),{"enabled":False,"kind":"messaging"})
        reader.assert_not_called()

    def test_partial_qa_configuration_and_production_qa_flag_fail(self):
        for context in ({"ACADEMY_QA_LEASE_ID":"a"*32},{"CANDIDATE_LEASE_REQUIRED":"true"},
                        dict(self.context,CANDIDATE_LEASE_REQUIRED="false"),
                        dict(self.context,ACADEMY_RUNTIME_ENV="production"),
                        dict(self.context,ACADEMY_QA_MODE="unknown")):
            with self.assertRaises(QaLeaseClosed):AdmissionGate("api",context=context)

    def test_cached_disabled_gate_cannot_bypass_later_partial_qa_configuration(self):
        import os
        from unittest.mock import patch
        from apps.infrastructure.qa_lease import get_admission_gate, _process_admission_gate

        _process_admission_gate.cache_clear()
        self.addCleanup(_process_admission_gate.cache_clear)
        with patch.dict(os.environ, {"ACADEMY_RUNTIME_ENV": "development"}, clear=True):
            ordinary = get_admission_gate("api")
            self.assertFalse(ordinary.enabled)
            self.assertIs(get_admission_gate("api"), ordinary)
            for key, value in (
                ("CANDIDATE_LEASE_REQUIRED", "true"),
                ("ACADEMY_QA_MODE", "isolated-qa"),
                ("CANDIDATE_LEASE_REQUIRED", "no"),
                ("ACADEMY_QA_LEASE_ID", "a" * 32),
            ):
                with patch.dict(os.environ, {key: value}):
                    with self.assertRaises(QaLeaseClosed):
                        get_admission_gate("api")
            self.assertIs(get_admission_gate("api"), ordinary)

    def test_cached_qa_gate_rejects_rebinding_without_replacing_activity(self):
        import os
        from unittest.mock import patch
        from apps.infrastructure.qa_lease import get_admission_gate

        gate = self.gate("api")
        with patch("apps.infrastructure.qa_lease._process_admission_gate", return_value=gate), \
             patch.dict(os.environ, self.context, clear=True):
            self.assertIs(get_admission_gate("api"), gate)
            with gate.begin("already-admitted", tenant_id=101):
                for key, value in (
                    ("ACADEMY_QA_LEASE_ID", "f" * 32),
                    ("ACADEMY_QA_BINDING_SHA256", "f" * 64),
                    ("ACADEMY_QA_MESSAGE_KEY_VERSION", "2"),
                    ("CANDIDATE_LEASE_REQUIRED", "false"),
                    ("ACADEMY_QA_MODE", ""),
                    ("ACADEMY_RUNTIME_ENV", "production"),
                ):
                    with patch.dict(os.environ, {key: value}):
                        with self.assertRaises(QaLeaseClosed):
                            get_admission_gate("api")
                        self.assertEqual(len(gate.snapshot()["inflight"]), 1)
            self.assertEqual(gate.snapshot()["inflight"], [])
            self.assertIs(get_admission_gate("api"), gate)

    def test_producer_overwrites_untrusted_stamp_and_checks_authoritative_tenant(self):
        gate=self.gate();payload=self.payload(gate)
        stamp=gate.validate_message(payload,tenant_id=101,job_id="job-123",job_metadata=self.metadata)
        self.assertNotIn("untrusted",stamp)
        self.assertEqual(stamp["tenant_id"],101)
        self.assertEqual(stamp["queue_kind"],"ai")
        for tenant in (102,None,True,101.2):
            with self.assertRaises(QaLeaseClosed):gate.stamp_message({},tenant,job_id="job-123",job_metadata=self.metadata)

    def test_cross_tenant_wrong_job_and_wrong_worker_rejected(self):
        payload=self.payload()
        for tenant,job in ((102,"job-123"),(None,"job-123"),(101,"other-job")):
            with self.assertRaises(QaLeaseClosed):
                self.gate().validate_message(payload,tenant_id=tenant,job_id=job,job_metadata=self.metadata)
        with self.assertRaises(QaLeaseClosed):
            self.gate("tools").validate_message(payload,tenant_id=101,job_id="job-123",job_metadata=self.metadata)

    def test_signature_body_and_stamp_tampering_fail(self):
        for field,value in (("question","changed"),("_qa_lease",{})):
            payload=self.payload();payload[field]=value
            with self.assertRaises(QaLeaseClosed):
                self.gate().validate_message(payload,tenant_id=101,job_id="job-123",job_metadata=self.metadata)
        for field,value in (("tenant_id",102),("lease_id","f"*32),("revision",2),
                            ("source_sha","f"*40),("issued_at",800)):
            payload=self.payload();payload["_qa_lease"][field]=value
            with self.assertRaises(QaLeaseClosed):
                self.gate().validate_message(payload,tenant_id=101,job_id="job-123",job_metadata=self.metadata)

    def test_unsigned_or_changed_outer_metadata_is_rejected_before_claim(self):
        gate=self.gate();payload=self.payload(gate)
        with self.assertRaises(QaLeaseClosed):
            gate.validate_message(payload,tenant_id=101,job_id="job-123")
        changed=dict(self.metadata,job_type="another_handler")
        with self.assertRaises(QaLeaseClosed):
            with gate.begin("receipt",tenant_id=101,message=payload,job_id="job-123",job_metadata=changed):
                self.fail("Tampered envelope reached the handler")
        self.assertFalse(self.claims.rows)
        with self.assertRaises(QaLeaseClosed):
            gate.stamp_message({},101,job_id="job-123")

    def test_canonical_types_duplicate_keys_and_nonfinite_json(self):
        from apps.infrastructure.qa_lease import decode_qa_json
        for raw in ('{"tier":"basic","tier":"premium"}', '{"nested":{"x":1,"x":2}}', '{"x":NaN}', '{"x":Infinity}'):
            with self.assertRaises(QaLeaseClosed):decode_qa_json(raw)
        self.assertEqual(decode_qa_json('{"b":2,"a":1}'),{"a":1,"b":2})
        gate=self.gate();payload=self.payload(gate)
        for value in (False,0.0,"0"):
            changed=dict(self.metadata,attempt=value)
            with self.assertRaises(QaLeaseClosed):
                gate.validate_message(payload,tenant_id=101,job_id="job-123",job_metadata=changed)
        reversed_metadata=dict(reversed(list(self.metadata.items())))
        gate.validate_message(payload,tenant_id=101,job_id="job-123",job_metadata=reversed_metadata)

    def test_revision_and_digest_change_reject_old_receipt(self):
        payload=self.payload();self.record["revision"]=2
        with self.assertRaises(QaLeaseClosed):
            self.gate().validate_message(payload,tenant_id=101,job_id="job-123",job_metadata=self.metadata)
        self.record["revision"]=1;self.record["images"]["ai"]="sha256:"+"f"*64
        with self.assertRaises(QaLeaseClosed):self.gate().admit()

    def test_clock_expiry_and_rotation_fail_closed(self):
        gate=self.gate();payload=self.payload(gate)
        self.now=999
        with self.assertRaises(QaLeaseClosed):gate.validate_message(payload,tenant_id=101,job_id="job-123",job_metadata=self.metadata)
        self.now=1570
        with self.assertRaises(QaLeaseClosed):gate.admit()
        self.now=1000
        gate.context["ACADEMY_QA_MESSAGE_KEY_VERSION"]="2"
        with self.assertRaises(QaLeaseClosed):gate.validate_message(payload,tenant_id=101,job_id="job-123",job_metadata=self.metadata)
        gate.context["ACADEMY_QA_MESSAGE_KEY_VERSION"]="1"
        gate.context["ACADEMY_QA_MESSAGE_SIGNING_KEY"]="invalid"
        with self.assertRaises(QaLeaseClosed):gate.stamp_message({},101,job_id="job-123",job_metadata=self.metadata)

    def test_unavailable_lease_and_missing_key_fail_before_processing(self):
        gate=self.gate();gate._reader=Mock(side_effect=RuntimeError("service unavailable"))
        with self.assertRaises(QaLeaseClosed):gate.admit()
        self.assertFalse(gate.snapshot()["lease_verified"])
        gate=self.gate();gate._key=Mock(side_effect=QaLeaseClosed("key unavailable"))
        with self.assertRaises(QaLeaseClosed):gate.stamp_message({},101,job_id="job-123",job_metadata=self.metadata)

    def test_receipt_hash_identity_and_expiry_do_not_interrupt_existing_work(self):
        gate=self.gate();payload=self.payload(gate)
        with gate.begin("job-123:receipt-one",tenant_id=101,message=payload,job_id="job-123",job_metadata=self.metadata):
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
        with gate.begin("receipt1",tenant_id=101,message=payload,job_id="job-123",job_metadata=self.metadata):
            with self.assertRaises(QaMessageInFlight):
                with other.begin("receipt2",tenant_id=101,message=payload,job_id="job-123",job_metadata=self.metadata):pass
            # No explicit completion: callback retry remains legal.
        with other.begin("receipt3",tenant_id=101,message=payload,job_id="job-123",job_metadata=self.metadata):
            other.complete_message(payload)
        with self.assertRaises(QaMessageCompleted):
            with gate.begin("receipt4",tenant_id=101,message=payload,job_id="job-123",job_metadata=self.metadata):pass
        self.assertFalse(gate.snapshot()["holds"])

    def test_rejection_is_identifiable_hold_and_not_queue_delete(self):
        gate=self.gate();payload=self.payload(gate)
        gate.hold_message(payload,reason="sensitive exception body must not be copied")
        self.assertEqual(gate.snapshot()["holds"][0]["message_id"],payload["_qa_lease"]["message_id"])
        self.assertNotIn("sensitive",json.dumps(gate.snapshot()))
        with self.assertRaises(QaLeaseClosed):gate.admit()

    def test_draining_inspection_is_metadata_only_and_admission_stays_closed(self):
        gate=self.gate("api")
        self.record.update(state="draining",revision=2,drain_from_revision=1)
        record=gate.inspect_lease()
        self.assertEqual(record["state"],"draining")
        self.assertEqual(record["drain_from_revision"],1)
        self.assertEqual(record["binding_sha256"],binding_sha256(self.record))
        with self.assertRaises(QaLeaseClosed):gate.admit()

    def test_control_hold_blocks_both_admission_and_auth_inspection(self):
        gate=self.gate("api");self.record["control_hold"]=True
        with self.assertRaises(QaLeaseClosed):gate.admit()
        with self.assertRaises(QaLeaseClosed):gate.inspect_lease()
        self.assertTrue(gate.snapshot()["control_hold"])

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

class MessagingAdmissionTests(unittest.TestCase):
    setUp=AdmissionTests.setUp
    gate=AdmissionTests.gate

    def message(self,gate):
        return gate.stamp_message({"tenant_id":101,"to":"synthetic-recipient","text":"synthetic",
                                   "business_idempotency_key":"job-123"},101,job_id="job-123")

    def run_worker(self,gate,payload,*,expired_in_poll=False,redis_available=True):
        import os
        from types import SimpleNamespace
        from unittest.mock import patch
        from apps.worker.messaging_worker import sqs_main as worker
        queue=Mock()
        def receive(**kwargs):
            worker._shutdown=True
            if expired_in_poll:self.now=1570
            return {"Body":json.dumps(payload),"ReceiptHandle":"synthetic-receipt","MessageId":"synthetic-id"}
        queue.receive_message.side_effect=receive
        def sleep(seconds):
            worker._shutdown=True
        cfg=SimpleNamespace(MESSAGING_SQS_QUEUE_NAME="academy-v1-development-messaging-queue",
                            SQS_WAIT_TIME_SECONDS=0,TEST_TENANT_ID=101)
        with patch.dict(os.environ,{"DJANGO_SETTINGS_MODULE":"","SOLAPI_MOCK":"true","REDIS_HOST":""}), \
             patch.object(worker,"get_admission_gate",return_value=gate), \
             patch.object(worker,"get_queue_client",return_value=queue), \
             patch.object(worker,"load_config",return_value=cfg), \
             patch.object(worker.signal,"signal"), \
             patch.object(worker.time,"sleep",side_effect=sleep), \
             patch.object(worker,"release_job_lock") as release, \
             patch.object(worker,"_get_solapi_client") as provider, \
             patch.object(worker,"acquire_job_lock",return_value=True) as acquire:
            if not redis_available:
                acquire.side_effect=worker.RedisLockUnavailableError("synthetic outage")
            worker._shutdown=False
            try:
                self.assertEqual(worker.main(),0)
            finally:
                worker._shutdown=False
            provider.assert_not_called()
        return queue,acquire,release

    def test_valid_mock_message_completes_and_duplicate_acks_without_reexecution(self):
        gate=self.gate("messaging");payload=self.message(gate)
        queue,acquire,release=self.run_worker(gate,payload)
        acquire.assert_called_once();release.assert_called_once()
        queue.delete_message.assert_called_once()
        self.assertEqual(self.claims.rows[payload["_qa_lease"]["message_id"]]["state"],"completed")
        queue,acquire,release=self.run_worker(gate,payload)
        queue.delete_message.assert_called_once();acquire.assert_not_called()
        self.assertFalse(gate.snapshot()["holds"])

    def test_journal_failure_cannot_ack_message(self):
        gate=self.gate("messaging");payload=self.message(gate)
        self.claims.finish=Mock(side_effect=QaLeaseClosed("outcome uncertain"))
        queue,acquire,_=self.run_worker(gate,payload)
        queue.delete_message.assert_not_called()
        self.assertTrue(gate.snapshot()["holds"])
        self.assertTrue(gate.snapshot()["inflight"])

    def test_tampered_and_expired_after_poll_never_reach_job_lock_or_delete(self):
        gate=self.gate("messaging");payload=self.message(gate);payload["tenant_id"]=102
        queue,acquire,_=self.run_worker(gate,payload)
        acquire.assert_not_called();queue.delete_message.assert_not_called()
        self.assertEqual(queue.change_message_visibility.call_args.kwargs["visibility_timeout"],0)
        self.assertTrue(gate.snapshot()["holds"])
        gate=self.gate("messaging");payload=self.message(gate)
        queue,acquire,_=self.run_worker(gate,payload,expired_in_poll=True)
        acquire.assert_not_called();queue.delete_message.assert_not_called()

    def test_inflight_duplicate_releases_without_hold_then_retry_succeeds(self):
        gate=self.gate("messaging");payload=self.message(gate)
        with gate.begin("other-receipt",tenant_id=101,message=payload,job_id="job-123"):
            queue,acquire,_=self.run_worker(gate,payload)
            acquire.assert_not_called();queue.delete_message.assert_not_called()
            self.assertEqual(queue.change_message_visibility.call_args.kwargs["visibility_timeout"],10)
            self.assertFalse(gate.snapshot()["holds"])
        queue,acquire,_=self.run_worker(gate,payload,redis_available=False)
        self.assertEqual(self.claims.rows[payload["_qa_lease"]["message_id"]]["state"],"retryable")
        queue,acquire,_=self.run_worker(gate,payload)
        queue.delete_message.assert_called_once()
        self.assertEqual(self.claims.rows[payload["_qa_lease"]["message_id"]]["state"],"completed")


class MessagingProducerTests(unittest.TestCase):
    setUp=AdmissionTests.setUp
    gate=AdmissionTests.gate

    def test_authoritative_source_tenant_is_stamped_before_sqs_send(self):
        from django.conf import settings
        from django.test import override_settings
        from unittest.mock import patch
        if not settings.configured:
            settings.configure(USE_TZ=True,SECRET_KEY="offline-synthetic-test")
        from apps.domains.messaging.sqs_queue import MessagingSQSQueue
        producer=MessagingSQSQueue.__new__(MessagingSQSQueue)
        producer.queue_client=Mock()
        producer.wake_messaging_workers=False
        gate=self.gate("api")
        with override_settings(MESSAGING_TENANT_BINDING_KEY="synthetic-test-key",
                               MESSAGING_SQS_QUEUE_NAME="academy-v1-development-messaging-queue"), \
             patch("apps.infrastructure.qa_lease.get_admission_gate",return_value=gate):
            self.assertTrue(producer.enqueue(tenant_id=1,source_tenant_id=101,
                to="01000000000",text="synthetic",message_mode="alimtalk"))
            sent=producer.queue_client.send_message.call_args.kwargs["message"]
            self.assertEqual(sent["_qa_lease"]["tenant_id"],101)
            self.assertEqual(sent["_qa_lease"]["queue_kind"],"messaging")
            self.gate("messaging").validate_message(
                sent,tenant_id=101,job_id=sent["business_idempotency_key"])
            producer.queue_client.reset_mock()
            with self.assertRaises(QaLeaseClosed):
                producer.enqueue(tenant_id=1,source_tenant_id=102,
                    to="01000000000",text="synthetic",message_mode="alimtalk")
            producer.queue_client.send_message.assert_not_called()
            with override_settings(MESSAGING_SQS_QUEUE_NAME="academy-v1-messaging-queue"):
                with self.assertRaises(QaLeaseClosed):
                    producer.enqueue(tenant_id=1,source_tenant_id=101,
                        to="01000000000",text="synthetic",message_mode="alimtalk")
            producer.queue_client.send_message.assert_not_called()
