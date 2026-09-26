"""The candidate QA ledger stays inert without trusted owner wiring."""
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading
import unittest

from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase

from apps.core.models import CandidateQaAction, CandidateQaReceipt, CandidateQaRun
from apps.infrastructure import qa_resource_receipts as receipts
from apps.infrastructure.qa_lease import binding_sha256


LEASE = "a" * 32
ACTION = "b" * 32
ARGUMENTS = "c" * 64
ROOT = "d" * 64
SOURCE = "e" * 40


def _record():
    record = {
        "lease_id": LEASE,
        "owner_task": "01a0d04a-d64f-7473-9f58-61a8e983dcc0",
        "lock_owner": "candidate:123:1",
        "source_sha": SOURCE,
        "images": {kind: "sha256:" + "f" * 64 for kind in ("api", "ai", "tools", "messaging")},
        "endpoint": "ssm://i-0123456789abcdef0:8000",
        "profile": "arn:aws:iam::809466760795:instance-profile/academy-api-qa",
        "scope": [509, 511],
        "baseline_sha256": "1" * 64,
        "tenant_ids": [101],
        "message_key_version": 1,
        "resource_manifest_sha256": ROOT,
        "state": "active", "revision": 2, "expires_at": 1600,
        "control_hold": False,
    }
    record["binding_sha256"] = binding_sha256(record)
    return record


def _root(record):
    return SimpleNamespace(
        sha256=ROOT,
        data={"schema": "academy-candidate-qa-root/v1", "lease_id": LEASE,
              "owner_task": record["owner_task"], "source_sha": SOURCE,
              "images": record["images"], "scope": [509, 511],
              "tenants": [{"id": 101}], "domains": ["auth", "ppt", "matchup", "omr"]},
    )


class Gate:
    enabled = True

    def __init__(self, record, kind):
        self.record = record
        self.kind = kind
        self.begins = 0
        self.barrier = None

    def inspect_lease(self):
        return self.record.copy()

    @contextmanager
    def begin(self, operation_id, *, tenant_id):
        self.begins += 1
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        yield self.record.copy()


def _ledger(record, *, policy=True, settlement=True, cleanup=False, fence=False,
            identity=None):
    gates = {kind: Gate(record, kind) for kind in receipts.ORIGIN_KINDS}
    ledger = receipts.CandidateQaReceiptLedger(
        root_provider=lambda current: _root(current), gate_provider=gates.__getitem__,
        action_policy=(lambda *args: True) if policy else None,
        settlement_authorizer=(lambda *args: True) if settlement else None,
        cleanup_authorizer=(lambda *args: True) if cleanup else None,
        fence_authorizer=(lambda *args: True) if fence else None,
        identity_reader=identity or (lambda: (receipts.EXPECTED_DATABASE,
                                            receipts.EXPECTED_ROLE)),
        clock=lambda: 1000,
    )
    return ledger, gates


def _effect(ledger, *, action_id=ACTION, args_sha256=ARGUMENTS):
    return ledger.effect(action_id=action_id, domain="omr", action_type="omr_grading",
                         tenant_id=101, args_sha256=args_sha256)


class CandidateQaReceiptLedgerTests(TestCase):
    def setUp(self):
        self.record = _record()
        self.ledger, self.gates = _ledger(self.record)
        self.ledger.ensure_run()

    def test_header_is_metadata_without_tenant_fk_and_rejects_foreign_root(self):
        run = CandidateQaRun.objects.get(pk=LEASE)
        self.assertEqual(run.tenant_ids, [101])
        self.assertEqual(run.root_sha256, ROOT)
        self.assertFalse(any(field.name == "tenant" for field in run._meta.fields))
        self.assertEqual(self.ledger.ensure_run(), LEASE)
        self.ledger.root_provider = lambda record: SimpleNamespace(sha256="0" * 64,
                                                                    data=_root(record).data)
        with self.assertRaises(receipts.QaReceiptHold):
            self.ledger.ensure_run()

    def test_actual_database_and_role_mismatch_holds_before_writes(self):
        self.ledger.identity_reader = lambda: ("production", receipts.EXPECTED_ROLE)
        with self.assertRaisesRegex(receipts.QaReceiptHold, "database/role"):
            with _effect(self.ledger):
                pass
        self.ledger.identity_reader = lambda: (receipts.EXPECTED_DATABASE, "postgres")
        with self.assertRaisesRegex(receipts.QaReceiptHold, "database/role"):
            self.ledger.snapshot(LEASE)
        self.assertFalse(CandidateQaAction.objects.exists())
        self.ledger.identity_reader = None
        if connection.vendor != "postgresql":
            with self.assertRaisesRegex(receipts.QaReceiptHold, "PostgreSQL"):
                self.ledger.snapshot(LEASE)

    def test_unwired_gate_policy_and_settlement_hold(self):
        self.ledger.action_policy = None
        with self.assertRaisesRegex(receipts.QaReceiptHold, "policy"):
            with _effect(self.ledger):
                pass
        self.ledger.action_policy = lambda *args: True
        self.ledger.gate_provider = None
        with self.assertRaisesRegex(receipts.QaReceiptHold, "gate"):
            with _effect(self.ledger):
                pass
        self.ledger.gate_provider = self.gates.__getitem__
        with _effect(self.ledger):
            pass
        self.ledger.settlement_authorizer = None
        with self.assertRaisesRegex(receipts.QaReceiptHold, "writer"):
            self.ledger.record_outcome(lease_id=LEASE, action_id=ACTION, event_id="1" * 32,
                                       outcome="unknown", proof_sha256="2" * 64)

    def test_same_action_id_replay_cannot_execute_and_changed_content_rejects(self):
        with _effect(self.ledger) as token:
            self.assertEqual(token.action_id, ACTION)
        with self.assertRaises(receipts.QaReceiptReplay):
            with _effect(self.ledger):
                self.fail("Duplicate action executed")
        with self.assertRaisesRegex(receipts.QaReceiptHold, "different content"):
            with _effect(self.ledger, args_sha256="3" * 64):
                self.fail("Changed action executed")
        run = CandidateQaRun.objects.get(pk=LEASE)
        self.assertEqual((run.action_count, run.version), (1, 1))
        self.assertEqual(self.ledger.snapshot(LEASE).unresolved_action_ids, (ACTION,))

    def test_multiple_physical_locators_and_late_event_invalidate_snapshot(self):
        with _effect(self.ledger):
            pass
        first = self.ledger.record_locator(lease_id=LEASE, action_id=ACTION,
                                           event_id="1" * 32, locator_kind="db_row",
                                           locator_type="exams.Exam", locator_ref="42")
        self.assertEqual(first, self.ledger.record_locator(
            lease_id=LEASE, action_id=ACTION, event_id="1" * 32,
            locator_kind="db_row", locator_type="exams.Exam", locator_ref="42"))
        with self.assertRaisesRegex(receipts.QaReceiptHold, "different content"):
            self.ledger.record_locator(lease_id=LEASE, action_id=ACTION,
                                       event_id="1" * 32, locator_kind="db_row",
                                       locator_type="exams.Exam", locator_ref="43")
        self.ledger.record_locator(lease_id=LEASE, action_id=ACTION,
                                   event_id="2" * 32, locator_kind="r2_object",
                                   locator_type="r2", locator_ref="4" * 64)
        self.ledger.record_outcome(lease_id=LEASE, action_id=ACTION,
                                   event_id="3" * 32, outcome="unknown",
                                   proof_sha256="5" * 64)
        self.assertEqual(self.ledger.snapshot(LEASE).unresolved_action_ids, (ACTION,))
        self.ledger.record_outcome(lease_id=LEASE, action_id=ACTION,
                                   event_id="4" * 32, outcome="success",
                                   proof_sha256="6" * 64)
        before = self.ledger.snapshot(LEASE)
        self.assertEqual(before.unresolved_action_ids, ())
        self.assertEqual(before.receipt_count, 4)
        self.ledger.record_locator(lease_id=LEASE, action_id=ACTION,
                                   event_id="5" * 32, locator_kind="r2_object",
                                   locator_type="r2", locator_ref="7" * 64)
        with self.assertRaisesRegex(receipts.QaReceiptHold, "changed"):
            self.ledger.assert_current(before)
        after = self.ledger.snapshot(LEASE)
        self.assertEqual(after.unresolved_action_ids, (ACTION,))
        self.assertEqual(after.receipt_count, 5)
        self.assertEqual(len({row[5] for row in after.receipts if row[3]}), 3)
        with self.assertRaisesRegex(receipts.QaReceiptHold, "external domain absence"):
            self.ledger.cleanup_zero(after)

    def test_expiry_and_drain_reject_new_effect_but_preserve_late_result(self):
        with _effect(self.ledger):
            pass
        self.record["state"] = "draining"
        with self.assertRaisesRegex(receipts.QaReceiptHold, "closed"):
            with _effect(self.ledger, action_id="8" * 32):
                pass
        self.ledger.record_outcome(lease_id=LEASE, action_id=ACTION,
                                   event_id="1" * 32, outcome="success",
                                   proof_sha256="2" * 64)
        self.assertEqual(self.ledger.snapshot(LEASE).unresolved_action_ids, ())
        self.record["state"] = "active"
        self.record["expires_at"] = 1029
        with self.assertRaisesRegex(receipts.QaReceiptHold, "closed"):
            with _effect(self.ledger, action_id="9" * 32):
                pass

    def test_renewal_does_not_invalidate_prior_action_result(self):
        with _effect(self.ledger):
            pass
        self.record["revision"] = 3
        self.record["expires_at"] = 1800
        self.ledger.record_outcome(lease_id=LEASE, action_id=ACTION,
                                   event_id="1" * 32, outcome="success",
                                   proof_sha256="2" * 64)
        self.assertEqual(CandidateQaAction.objects.get(action_id=ACTION).operation_revision, 2)
        self.assertEqual(self.ledger.snapshot(LEASE).unresolved_action_ids, ())

    def test_outer_database_rollback_reverts_auto_id_locator_mapping(self):
        with _effect(self.ledger):
            pass
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with transaction.atomic():
                self.ledger.record_locator(lease_id=LEASE, action_id=ACTION,
                                           event_id="1" * 32, locator_kind="db_row",
                                           locator_type="exams.Exam", locator_ref="42")
                raise RuntimeError("rollback")
        self.assertEqual(self.ledger.snapshot(LEASE).receipt_count, 0)

    def test_cleanup_and_fence_need_separate_trusted_providers(self):
        with _effect(self.ledger):
            pass
        with self.assertRaisesRegex(receipts.QaReceiptHold, "cleanup fence"):
            self.ledger.record_cleanup(lease_id=LEASE, action_id=ACTION,
                                       event_id="1" * 32, proof_sha256="2" * 64)
        with self.assertRaisesRegex(receipts.QaReceiptHold, "writer fence"):
            self.ledger.fence(LEASE)
        self.ledger.fence_authorizer = lambda snapshot: True
        with self.assertRaisesRegex(receipts.QaReceiptHold, "Unresolved"):
            self.ledger.fence(LEASE)
        self.ledger.record_outcome(lease_id=LEASE, action_id=ACTION,
                                   event_id="3" * 32, outcome="failure",
                                   proof_sha256="4" * 64)
        self.assertEqual(self.ledger.fence(LEASE), 3)
        with self.assertRaisesRegex(receipts.QaReceiptHold, "fenced"):
            self.ledger.record_outcome(lease_id=LEASE, action_id=ACTION,
                                       event_id="5" * 32, outcome="success",
                                       proof_sha256="6" * 64)
        self.assertTrue(self.ledger.snapshot(LEASE).fenced)

    def test_late_physical_locator_invalidates_fence_observation(self):
        with _effect(self.ledger):
            pass
        self.ledger.record_outcome(lease_id=LEASE, action_id=ACTION,
                                   event_id="1" * 32, outcome="success",
                                   proof_sha256="2" * 64)

        def late_locator(snapshot):
            self.ledger.record_locator(lease_id=LEASE, action_id=ACTION,
                                       event_id="3" * 32, locator_kind="r2_object",
                                       locator_type="r2", locator_ref="4" * 64)
            return True

        self.ledger.fence_authorizer = late_locator
        with self.assertRaisesRegex(receipts.QaReceiptHold, "changed during"):
            self.ledger.fence(LEASE)
        self.assertEqual(self.ledger.snapshot(LEASE).unresolved_action_ids, (ACTION,))

    def test_full_snapshot_pages_and_capacity_hold(self):
        with _effect(self.ledger):
            pass
        previous = receipts.PAGE_SIZE
        previous_capacity = receipts.MAX_ACTIONS
        receipts.PAGE_SIZE = 1
        receipts.MAX_ACTIONS = 1
        try:
            for number in range(3):
                self.ledger.record_locator(lease_id=LEASE, action_id=ACTION,
                                           event_id=f"{number+1:032x}", locator_kind="db_row",
                                           locator_type="exams.Exam", locator_ref=str(number + 1))
            snapshot = self.ledger.snapshot(LEASE)
            self.assertEqual(snapshot.receipt_count, len(snapshot.receipts))
            self.assertEqual(snapshot.receipt_count, 3)
            with self.assertRaisesRegex(receipts.QaReceiptHold, "capacity"):
                with _effect(self.ledger, action_id="f" * 32):
                    pass
        finally:
            receipts.PAGE_SIZE = previous
            receipts.MAX_ACTIONS = previous_capacity


class CandidateQaReceiptConcurrencyPgTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("PostgreSQL row locks and immutable triggers require PG CI")
        super().setUpClass()

    def test_database_trigger_rejects_receipt_rewrite_and_delete(self):
        record = _record()
        ledger, _ = _ledger(record)
        ledger.ensure_run()
        with _effect(ledger):
            pass
        pk = ledger.record_outcome(lease_id=LEASE, action_id=ACTION,
                                   event_id="1" * 32, outcome="unknown",
                                   proof_sha256="2" * 64)
        with self.assertRaises(Exception):
            with transaction.atomic():
                CandidateQaReceipt.objects.filter(pk=pk).update(outcome="failure")
        with self.assertRaises(Exception):
            with transaction.atomic():
                CandidateQaReceipt.objects.filter(pk=pk).delete()
        self.assertEqual(CandidateQaReceipt.objects.get(pk=pk).outcome, "unknown")

    def test_actual_postgres_test_database_cannot_impersonate_development(self):
        record = _record()
        ledger, _ = _ledger(record)
        ledger.identity_reader = None
        with self.assertRaisesRegex(receipts.QaReceiptHold, "database/role"):
            ledger.ensure_run()
        self.assertFalse(CandidateQaRun.objects.exists())

    def test_same_action_race_serializes_to_one_planned_intent(self):
        record = _record()
        ledger, gates = _ledger(record)
        ledger.ensure_run()
        gates["api"].barrier = threading.Barrier(2)

        def start():
            close_old_connections()
            try:
                try:
                    with _effect(ledger):
                        return "created"
                except receipts.QaReceiptReplay:
                    return "replay"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=15) for future in (pool.submit(start), pool.submit(start))]
        self.assertEqual(sorted(results), ["created", "replay"])
        run = CandidateQaRun.objects.get(pk=LEASE)
        self.assertEqual((run.action_count, run.version), (1, 1))
        self.assertEqual(CandidateQaAction.objects.filter(run=run).count(), 1)
