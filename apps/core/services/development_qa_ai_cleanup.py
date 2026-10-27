"""AI residue for capability-owned disposable development QA, never retention.

The fixed SSM entrypoint owns the surrounding transaction and tenant cleanup.
Receipts retain only exact job IDs/counts and the existing ownership hash, so
post-delete Inspect can check the same Redis keys without storing credentials.
"""
import hashlib
import hmac
import os
import re

from django.db import connection

from apps.core.management.commands.setup_ymath_realuse_scenario import assert_isolated_runtime
from apps.core.models import OpsAuditLog, Tenant
from apps.domains.ai.models import AIJobModel, AIResultModel
from libs.redis.client import get_redis_client


MAX_JOBS = 1000
TERMINAL = frozenset({"DONE", "FAILED", "REJECTED_BAD_INPUT", "REVIEW_REQUIRED"})
RECEIPT_ACTION = "development.qa.ai.cleanup"
FIELDS = ("ai_jobs", "ai_results", "ai_redis_keys")


class OwnedQaAI:
    def __init__(self, tenant_code, tenant_id, capability):
        assert_isolated_runtime()
        if (os.environ.get("REDIS_HOST") != "127.0.0.1"
                or os.environ.get("REDIS_PORT", "6379") != "6379"):
            raise PermissionError("QA AI cache must be the isolated host-local service")
        if (not re.fullmatch(r"qa-ymath-realuse-fe-[0-9]+-[0-9]+-[a-f0-9]{12}", tenant_code)
                or type(tenant_id) is not int or tenant_id <= 0
                or not re.fullmatch(r"[a-f0-9]{64}", capability)):
            raise PermissionError("Invalid QA AI scope")
        self.code, self.tenant_id = tenant_code, tenant_id
        self.owner = {
            "schema": "frontend-development-qa/v1", "tenant_code": tenant_code,
            "tenant_id": tenant_id, "owner_sha256": hashlib.sha256(
                f"{tenant_code}:{tenant_id}:{capability}".encode()).hexdigest(),
        }
        # SET_NULL preserves this setup seal after tenant deletion.
        seals = list(OpsAuditLog.objects.filter(
            action="development.qa.setup", result="success",
            payload__tenant_code=tenant_code, payload__tenant_id=tenant_id,
        )[:2])
        if len(seals) != 1 or not self._same_owner(seals[0].payload):
            raise PermissionError("Missing or ambiguous QA AI ownership")
        self.seal = seals[0]
        tenants = list(Tenant.objects.filter(pk=tenant_id).values_list("code", flat=True))
        if tenants not in ([], [tenant_code]) or self.seal.target_tenant_id not in (None, tenant_id):
            raise PermissionError("QA AI tenant identity mismatch")
        self.present = bool(tenants)
        self.redis = get_redis_client()
        if self.redis is None:
            raise RuntimeError("QA AI cache readback unavailable")
        self.redis.ping()

    def _same_owner(self, owner):
        return (isinstance(owner, dict) and set(owner) == set(self.owner)
                and all(owner[k] == self.owner[k] for k in ("schema", "tenant_code", "tenant_id"))
                and isinstance(owner["owner_sha256"], str)
                and hmac.compare_digest(owner["owner_sha256"], self.owner["owner_sha256"]))

    def _receipts(self):
        records = list(OpsAuditLog.objects.filter(
            action=RECEIPT_ACTION, result="success",
            payload__owner__tenant_code=self.code, payload__owner__tenant_id=self.tenant_id,
        )[:2])
        if len(records) > 1:
            raise PermissionError("Ambiguous QA AI receipt")
        if not records:
            return None
        receipt = records[0].payload
        if (not isinstance(receipt, dict) or set(receipt) != {"owner", "job_ids", "before"}
                or not self._same_owner(receipt["owner"])):
            raise PermissionError("Invalid QA AI receipt")
        ids = receipt["job_ids"]
        self._validate_ids(ids)
        before = receipt["before"]
        if (not isinstance(before, dict) or set(before) != set(FIELDS)
                or any(type(n) is not int or n < 0 for n in before.values())
                or before["ai_jobs"] != len(ids) or before["ai_results"] > len(ids)
                or before["ai_redis_keys"] > len(ids) * 4):
            raise PermissionError("Invalid QA AI receipt counts")
        return receipt

    @staticmethod
    def _validate_ids(ids):
        if (not isinstance(ids, list) or len(ids) > MAX_JOBS
                or any(not isinstance(j, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", j) for j in ids)
                or ids != sorted(set(ids))):
            raise PermissionError("Invalid QA AI job inventory")

    def _jobs(self, *, lock=False):
        query = AIJobModel.objects.filter(tenant_id=str(self.tenant_id)).order_by("job_id")
        if lock:
            query = query.select_for_update()
        jobs = list(query[:MAX_JOBS + 1])
        self._validate_ids([j.job_id for j in jobs])
        for job in jobs:
            # A reused ID, pre-seal data, or conflicting payload is not owned.
            if (job.created_at < self.seal.created_at or not isinstance(job.payload, dict)
                    or (job.payload.get("tenant_id") is not None
                        and str(job.payload["tenant_id"]) != str(self.tenant_id))):
                raise PermissionError("QA AI job ownership mismatch")
        return jobs

    def _keys(self, ids):
        return [key for job_id in ids for key in (
            f"tenant:{self.tenant_id}:job:{job_id}:status",
            f"tenant:{self.tenant_id}:job:{job_id}:progress",
            f"job:{job_id}:status", f"job:{job_id}:progress",
        )]

    def _residue(self, ids):
        keys = self._keys(ids)
        # Unknown keys are counted, never adopted or deleted. Bounded SCAN also
        # detects caches whose job row was removed outside this operation.
        namespaced = set()
        cursor = 0
        for _ in range(1000):
            cursor, batch = self.redis.scan(
                cursor=cursor, match=f"tenant:{self.tenant_id}:job:*", count=100)
            namespaced.update(k.decode() if isinstance(k, bytes) else k for k in batch)
            if len(namespaced) > MAX_JOBS * 4:
                raise RuntimeError("QA AI cache inventory limit exceeded")
            if int(cursor) == 0:
                break
        else:
            raise RuntimeError("QA AI cache inventory incomplete")
        unknown = namespaced - set(keys)
        exact_count = self.redis.exists(*keys) if keys else 0
        return {
            "ai_jobs": AIJobModel.objects.filter(tenant_id=str(self.tenant_id)).count(),
            "ai_results": AIResultModel.objects.filter(job__tenant_id=str(self.tenant_id)).count(),
            "ai_redis_keys": len(unknown) + exact_count,
        }, unknown

    def inspect(self):
        jobs = self._jobs()
        receipt = self._receipts()
        if not self.present and receipt is None:
            raise PermissionError("Absent QA AI scope requires a cleanup receipt")
        ids = sorted(set([j.job_id for j in jobs] + (receipt["job_ids"] if receipt else [])))
        self._validate_ids(ids)
        residue, _unknown = self._residue(ids)
        return residue

    def prepare(self):
        if not connection.in_atomic_block or not self.present or self._receipts() is not None:
            raise PermissionError("QA AI cleanup requires a live owned transaction")
        jobs = self._jobs(lock=True)
        for job in jobs:
            if (job.status not in TERMINAL or job.completed_at is None
                    or job.locked_by or job.locked_at or job.lease_expires_at):
                raise PermissionError("QA AI job is not terminal and unleased")
        ids = [j.job_id for j in jobs]
        before, unknown = self._residue(ids)
        if unknown or before["ai_jobs"] != len(ids):
            raise PermissionError("QA AI inventory changed or contains unowned cache")
        return {"owner": self.owner.copy(), "job_ids": ids, "before": before}

    def delete(self, plan):
        # Recheck the exact inventory after potentially slow R2 cleanup.
        if self.prepare() != plan:
            raise PermissionError("QA AI cleanup plan changed")
        OpsAuditLog.objects.create(
            action=RECEIPT_ACTION, actor_username="frontend-release-runner",
            target_tenant_id=self.tenant_id, payload=plan, result="success")
        keys = self._keys(plan["job_ids"])
        if keys:
            self.redis.delete(*keys)
        if keys and self.redis.exists(*keys):
            raise RuntimeError("QA AI cache cleanup incomplete")
        AIJobModel.objects.filter(
            tenant_id=str(self.tenant_id), job_id__in=plan["job_ids"],
        ).delete()  # Only the associated AIResult rows cascade.
        residue = self.inspect()
        if any(residue.values()):
            raise RuntimeError("QA AI cleanup readback is not zero")
        return residue
