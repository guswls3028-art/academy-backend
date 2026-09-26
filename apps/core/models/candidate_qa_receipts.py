"""Durable candidate QA metadata; never cascade it with disposable tenants."""

from django.db import models


class CandidateQaRun(models.Model):
    lease_id = models.CharField(max_length=32, primary_key=True)
    root_sha256 = models.CharField(max_length=64)
    binding_sha256 = models.CharField(max_length=64)
    owner_task = models.CharField(max_length=36)
    source_sha = models.CharField(max_length=40)
    tenant_ids = models.JSONField()
    domains = models.JSONField()
    version = models.PositiveBigIntegerField(default=0)
    action_count = models.PositiveIntegerField(default=0)
    receipt_count = models.PositiveIntegerField(default=0)
    fenced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "candidate_qa_run"


class CandidateQaAction(models.Model):
    run = models.ForeignKey(CandidateQaRun, on_delete=models.PROTECT, related_name="actions")
    action_id = models.CharField(max_length=32)
    domain = models.CharField(max_length=16)
    action_type = models.CharField(max_length=48)
    tenant_id = models.PositiveBigIntegerField()
    origin_kind = models.CharField(max_length=16)
    operation_revision = models.PositiveBigIntegerField()
    args_sha256 = models.CharField(max_length=64)
    intent_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "candidate_qa_action"
        constraints = [
            models.UniqueConstraint(fields=["run", "action_id"], name="uniq_candidate_qa_action"),
        ]
        indexes = [
            models.Index(fields=["run", "domain", "tenant_id"], name="candidate_qa_action_scope_idx"),
        ]


class CandidateQaReceipt(models.Model):
    class Kind(models.TextChoices):
        LOCATOR = "locator", "Locator"
        OUTCOME = "outcome", "Outcome"
        CLEANUP = "cleanup", "Cleanup evidence"

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Confirmed success"
        FAILURE = "failure", "Confirmed failure"
        UNKNOWN = "unknown", "Uncertain result"

    action = models.ForeignKey(CandidateQaAction, on_delete=models.PROTECT, related_name="receipts")
    event_id = models.CharField(max_length=32)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    locator_kind = models.CharField(max_length=24, blank=True, default="")
    locator_type = models.CharField(max_length=80, blank=True, default="")
    locator_ref = models.CharField(max_length=64, blank=True, default="")
    outcome = models.CharField(max_length=16, choices=Outcome.choices, blank=True, default="")
    proof_sha256 = models.CharField(max_length=64, blank=True, default="")
    event_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "candidate_qa_receipt"
        constraints = [
            models.UniqueConstraint(fields=["action", "event_id"], name="uniq_candidate_qa_receipt"),
        ]
        indexes = [
            models.Index(fields=["action", "id"], name="candidate_qa_receipt_order_idx"),
        ]
