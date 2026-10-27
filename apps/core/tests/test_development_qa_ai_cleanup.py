import hashlib
import os
from datetime import timedelta
from unittest.mock import patch

from django.db import transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.core.models import OpsAuditLog, Tenant
from apps.core.services.development_qa_ai_cleanup import OwnedQaAI, RECEIPT_ACTION
from apps.domains.ai.models import AIJobModel, AIResultModel


class MemoryRedis:
    def __init__(self):
        self.keys = set()
        self.deleted = []
        self.refuse_delete = False

    def ping(self):
        return True

    def scan(self, *, cursor, match, count):
        return 0, [k for k in self.keys if k.startswith(match[:-1])]

    def exists(self, *keys):
        return sum(k in self.keys for k in keys)

    def delete(self, *keys):
        self.deleted.extend(keys)
        if not self.refuse_delete:
            self.keys.difference_update(keys)


class OwnedQaAICleanupTests(TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"REDIS_HOST": "127.0.0.1", "REDIS_PORT": "6379"}))
        self.tenant = Tenant.objects.create(code="qa-ymath-realuse-fe-123-1-abcdef123456", name="QA")
        self.tenant_id = self.tenant.pk
        self.code = self.tenant.code
        self.capability = "a" * 64
        self.owner = {
            "schema": "frontend-development-qa/v1", "tenant_code": self.code,
            "tenant_id": self.tenant_id, "owner_sha256": hashlib.sha256(
                f"{self.code}:{self.tenant_id}:{self.capability}".encode()).hexdigest(),
        }
        self.seal = OpsAuditLog.objects.create(
            action="development.qa.setup", target_tenant=self.tenant,
            payload=self.owner, result="success")
        self.redis = MemoryRedis()
        self.enterContext(patch(
            "apps.core.services.development_qa_ai_cleanup.get_redis_client", return_value=self.redis))

    def scope(self, **kwargs):
        return OwnedQaAI(kwargs.get("code", self.code), kwargs.get("tenant_id", self.tenant_id),
                         kwargs.get("capability", self.capability))

    def job(self, job_id="owned-job", **kwargs):
        defaults = {"tenant_id": str(self.tenant_id), "status": "DONE", "job_type": "omr_grading",
                    "completed_at": timezone.now(), "payload": {"tenant_id": self.tenant_id}}
        defaults.update(kwargs)
        job = AIJobModel.objects.create(job_id=job_id, **defaults)
        AIResultModel.objects.create(job=job, payload={"answer": "synthetic"})
        return job

    def test_exact_terminal_cleanup_preserves_foreign_rows_keys_and_manual_data(self):
        job = self.job()
        foreign = self.job("foreign-job", tenant_id="99999", payload={"tenant_id": 99999})
        ai = self.scope()
        owned_keys = ai._keys([job.job_id])
        foreign_keys = ["tenant:99999:job:foreign-job:status", "job:foreign-job:progress", "unrelated"]
        self.redis.keys.update(owned_keys + foreign_keys)
        with transaction.atomic():
            plan = ai.prepare()
            self.assertEqual(plan["before"], {"ai_jobs": 1, "ai_results": 1, "ai_redis_keys": 4})
            self.assertEqual(ai.delete(plan), {"ai_jobs": 0, "ai_results": 0, "ai_redis_keys": 0})
        self.assertEqual(set(self.redis.deleted), set(owned_keys))
        self.assertEqual(self.redis.keys, set(foreign_keys))
        self.assertEqual(list(AIJobModel.objects.values_list("pk", flat=True)), [foreign.pk])
        self.assertEqual(AIResultModel.objects.count(), 1)
        self.assertTrue(Tenant.objects.filter(pk=self.tenant_id).exists())

    def test_receipt_survives_tenant_deletion_and_post_inspect_detects_late_cache(self):
        self.job()
        ai = self.scope()
        with transaction.atomic():
            ai.delete(ai.prepare())
            self.tenant.delete()
        receipt = OpsAuditLog.objects.get(action=RECEIPT_ACTION)
        self.assertIsNone(receipt.target_tenant_id)
        self.assertNotIn(self.capability, str(receipt.payload))
        self.assertFalse(any(self.scope().inspect().values()))
        self.redis.keys.add("job:owned-job:status")
        self.assertEqual(self.scope().inspect()["ai_redis_keys"], 1)

    def test_nonterminal_unfinished_and_leased_jobs_preserved(self):
        job = self.job()
        for changes in [{"status": s} for s in ("PENDING", "VALIDATING", "RUNNING", "RETRYING", "FALLBACK_TO_GPU")
                        ] + [{"completed_at": None}, {"locked_by": "worker"},
                             {"locked_at": timezone.now()}, {"lease_expires_at": timezone.now()}]:
            with self.subTest(changes=changes):
                AIJobModel.objects.filter(pk=job.pk).update(
                    status="DONE", completed_at=timezone.now(), locked_by=None,
                    locked_at=None, lease_expires_at=None)
                AIJobModel.objects.filter(pk=job.pk).update(**changes)
                with self.assertRaises(PermissionError):
                    self.scope().prepare()
                self.assertEqual(AIResultModel.objects.count(), 1)
                self.assertEqual(self.redis.deleted, [])
        self.assertFalse(OpsAuditLog.objects.filter(action=RECEIPT_ACTION).exists())

    def test_ownership_failures_never_touch_cache_or_jobs(self):
        self.job()
        for args in ({"capability": "b" * 64}, {"tenant_id": self.tenant_id + 1},
                     {"code": "ordinary-user-tenant"}):
            with self.subTest(args=args), self.assertRaises(PermissionError):
                self.scope(**args)
        self.seal.delete()
        with self.assertRaises(PermissionError):
            self.scope()
        self.assertEqual(AIJobModel.objects.count(), 1)
        self.assertEqual(self.redis.deleted, [])

    def test_ambiguous_seal_and_payload_tenant_mismatch_fail_closed(self):
        self.job(payload={"tenant_id": "99999"})
        with self.assertRaises(PermissionError):
            self.scope().prepare()
        OpsAuditLog.objects.create(action="development.qa.setup", payload=self.owner, result="success")
        with self.assertRaises(PermissionError):
            self.scope()
        self.assertEqual(self.redis.deleted, [])

    def test_pre_seal_rows_and_unknown_namespaced_keys_are_not_adopted(self):
        job = self.job()
        AIJobModel.objects.filter(pk=job.pk).update(created_at=self.seal.created_at - timedelta(seconds=1))
        with self.assertRaises(PermissionError):
            self.scope().prepare()
        AIJobModel.objects.filter(pk=job.pk).update(created_at=timezone.now())
        self.redis.keys.add(f"tenant:{self.tenant_id}:job:unknown:status")
        self.assertEqual(self.scope().inspect()["ai_redis_keys"], 1)
        with self.assertRaises(PermissionError):
            self.scope().prepare()
        self.assertEqual(self.redis.deleted, [])

    def test_absent_without_receipt_is_unproven_even_with_setup_seal(self):
        self.tenant.delete()
        with self.assertRaises(PermissionError):
            self.scope().inspect()

    def test_empty_run_has_durable_zero_receipt(self):
        ai = self.scope()
        with transaction.atomic():
            ai.delete(ai.prepare())
            self.tenant.delete()
        self.assertFalse(any(self.scope().inspect().values()))
        self.assertEqual(OpsAuditLog.objects.get(action=RECEIPT_ACTION).payload["job_ids"], [])

    def test_cache_unavailable_or_delete_failure_cannot_claim_zero(self):
        self.job()
        with patch("apps.core.services.development_qa_ai_cleanup.get_redis_client", return_value=None):
            with self.assertRaises(RuntimeError):
                self.scope()
        ai = self.scope()
        self.redis.keys.update(ai._keys(["owned-job"]))
        self.redis.refuse_delete = True
        with self.assertRaises(RuntimeError), transaction.atomic():
            ai.delete(ai.prepare())
        self.assertEqual(AIJobModel.objects.count(), 1)
        self.assertFalse(OpsAuditLog.objects.filter(action=RECEIPT_ACTION).exists())

    def test_rollback_and_retry_rebuild_exact_plan(self):
        self.job()
        ai = self.scope()
        self.redis.keys.update(ai._keys(["owned-job"]))
        with self.assertRaises(RuntimeError), transaction.atomic():
            ai.delete(ai.prepare())
            raise RuntimeError("later tenant destroy failed")
        self.assertEqual(AIJobModel.objects.count(), 1)
        self.assertFalse(OpsAuditLog.objects.filter(action=RECEIPT_ACTION).exists())
        with transaction.atomic():
            retry = self.scope()
            self.assertFalse(any(retry.delete(retry.prepare()).values()))

    def test_inventory_change_after_preflight_is_preserved(self):
        self.job()
        ai = self.scope()
        plan = ai.prepare()
        self.job("late-job")
        with self.assertRaises(PermissionError):
            ai.delete(plan)
        self.assertEqual(AIJobModel.objects.count(), 2)
        self.assertEqual(self.redis.deleted, [])

    def test_tampered_receipt_and_incomplete_scan_fail_closed(self):
        ai = self.scope()
        with transaction.atomic():
            ai.delete(ai.prepare())
        receipt = OpsAuditLog.objects.get(action=RECEIPT_ACTION)
        receipt.payload["job_ids"] = ["../foreign"]
        receipt.save(update_fields=["payload"])
        with self.assertRaises(PermissionError):
            self.scope().inspect()
        receipt.delete()
        with patch.object(self.redis, "scan", return_value=(1, [])):
            with self.assertRaises(RuntimeError):
                self.scope().inspect()


class QaAIRedisBoundaryTests(SimpleTestCase):
    def test_nonlocal_or_unproven_cache_is_rejected_before_database_or_cache_access(self):
        for host, port in [("", "6379"), ("production-cache.example", "6379"), ("127.0.0.1", "6380")]:
            with self.subTest(host=host, port=port), patch.dict(
                os.environ, {"REDIS_HOST": host, "REDIS_PORT": port},
            ), patch("apps.core.services.development_qa_ai_cleanup.assert_isolated_runtime"), patch(
                "apps.core.services.development_qa_ai_cleanup.get_redis_client",
            ) as redis:
                with self.assertRaises(PermissionError):
                    OwnedQaAI("qa-ymath-realuse-fe-123-1-abcdef123456", 71, "a" * 64)
                redis.assert_not_called()
