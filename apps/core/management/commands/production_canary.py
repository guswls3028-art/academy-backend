from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, Iterable

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q
from django.utils import timezone

from apps.core.management.commands.cleanup_e2e_residue import matches_residue
from apps.core.models import Tenant
from apps.core.models.program import Program
from apps.domains.messaging.services.preflight import build_messaging_operations_status
from apps.domains.video.models import Video, VideoTranscodeJob


@dataclass(frozen=True)
class CanaryCheck:
    name: str
    severity: str
    ok: bool
    detail: str
    data: dict[str, Any]


class Command(BaseCommand):
    help = "Read-only production canary for tenant DB/runtime invariants."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-id",
            type=int,
            default=1,
            help="Tenant id to check. Defaults to production owner tenant 1.",
        )
        parser.add_argument(
            "--tenant-code",
            default="",
            help="Optional tenant code. If provided, it must match --tenant-id.",
        )
        parser.add_argument(
            "--fail-on-warning",
            action="store_true",
            help="Return non-zero when warning checks are present.",
        )
        parser.add_argument(
            "--video-stale-minutes",
            type=int,
            default=120,
            help="Age threshold for uploaded/processing video canary checks.",
        )
        parser.add_argument(
            "--sample-limit",
            type=int,
            default=5,
            help="Maximum sample rows per residue category.",
        )
        parser.add_argument(
            "--indent",
            type=int,
            default=None,
            help="Pretty JSON indent. Defaults to compact JSON.",
        )
        parser.add_argument(
            "--allow-fee-management-tenant-id",
            type=int,
            action="append",
            default=[],
            help="Allowlisted tenant id for fee_management feature flag. May be repeated.",
        )

    def handle(self, *args, **options):
        tenant = self._resolve_tenant(
            tenant_id=options["tenant_id"],
            tenant_code=(options["tenant_code"] or "").strip(),
        )
        fail_on_warning = bool(options["fail_on_warning"])

        checks: list[CanaryCheck] = []
        checks.extend(self._check_core_tenant(tenant))
        checks.extend(self._check_messaging(tenant))
        checks.extend(
            self._check_video(
                tenant,
                stale_minutes=max(1, int(options["video_stale_minutes"])),
            )
        )
        checks.extend(
            self._check_e2e_residue(
                tenant,
                sample_limit=max(1, int(options["sample_limit"])),
            )
        )
        checks.extend(
            self._check_billing(
                tenant,
                allow_fee_management_tenant_ids=set(options["allow_fee_management_tenant_id"] or []),
            )
        )

        error_count = sum(1 for check in checks if check.severity == "error" and not check.ok)
        warning_count = sum(1 for check in checks if check.severity == "warning" and not check.ok)
        ok = error_count == 0 and (warning_count == 0 or not fail_on_warning)
        payload = {
            "ok": ok,
            "checked_at": timezone.now().isoformat(),
            "tenant": {
                "id": tenant.id,
                "code": tenant.code,
                "name": tenant.name,
                "is_active": tenant.is_active,
            },
            "fail_on_warning": fail_on_warning,
            "summary": {
                "checks": len(checks),
                "errors": error_count,
                "warnings": warning_count,
            },
            "checks": [asdict(check) for check in checks],
        }
        self.stdout.write(json.dumps(payload, ensure_ascii=True, indent=options["indent"], default=str))

        if not ok:
            raise CommandError(
                f"production_canary failed: errors={error_count} warnings={warning_count}"
            )

    def _resolve_tenant(self, *, tenant_id: int, tenant_code: str) -> Tenant:
        try:
            tenant = Tenant.objects.get(id=tenant_id)
        except Tenant.DoesNotExist:
            raise CommandError(f"tenant_id={tenant_id} not found")

        if tenant_code and tenant.code != tenant_code:
            raise CommandError(
                f"tenant mismatch: id={tenant_id} has code={tenant.code!r}, expected {tenant_code!r}"
            )
        return tenant

    def _check_core_tenant(self, tenant: Tenant) -> list[CanaryCheck]:
        program = Program.objects.filter(tenant=tenant).first()
        return [
            CanaryCheck(
                name="tenant_active",
                severity="error",
                ok=bool(tenant.is_active),
                detail="Tenant must be active for production traffic.",
                data={"tenant_id": tenant.id, "tenant_code": tenant.code},
            ),
            CanaryCheck(
                name="program_exists",
                severity="error",
                ok=program is not None,
                detail="Program is the tenant-level product SSOT and must exist.",
                data={"tenant_id": tenant.id},
            ),
        ]

    def _check_messaging(self, tenant: Tenant) -> list[CanaryCheck]:
        status = build_messaging_operations_status(tenant)
        auto_send = status["auto_send"]
        scheduled = status["scheduled"]
        log_24h = status["log_24h"]
        worker = status["worker"]

        auto_send_bad = (
            int(auto_send["enabled_without_template"])
            + int(auto_send["enabled_unapproved_template"])
            + int(auto_send["enabled_manual_only"])
        )
        auto_send_data = {
            **auto_send,
            "samples": self._sample_autosend_risks(tenant),
        }

        return [
            CanaryCheck(
                name="messaging_autosend_ready",
                severity="error",
                ok=auto_send_bad == 0,
                detail="Enabled AutoSendConfig rows must have an implemented trigger and approved effective template.",
                data=auto_send_data,
            ),
            CanaryCheck(
                name="messaging_scheduled_overdue",
                severity="warning",
                ok=int(scheduled["overdue"]) == 0,
                detail="Scheduled notifications should not remain overdue.",
                data=scheduled,
            ),
            CanaryCheck(
                name="messaging_recent_failures",
                severity="warning",
                ok=int(log_24h["failed"]) == 0 and int(scheduled["failed_24h"]) == 0,
                detail="Recent messaging failures should be investigated before they turn into user complaints.",
                data={"log_24h": log_24h, "scheduled": scheduled},
            ),
            CanaryCheck(
                name="messaging_worker_heartbeat",
                severity="warning",
                ok=worker["status"] == "ok",
                detail="Messaging worker heartbeat should be fresh.",
                data=worker,
            ),
        ]

    def _check_video(self, tenant: Tenant, *, stale_minutes: int) -> list[CanaryCheck]:
        now = timezone.now()
        stale_cutoff = now - timedelta(minutes=stale_minutes)
        active_states = [
            VideoTranscodeJob.State.QUEUED,
            VideoTranscodeJob.State.RUNNING,
            VideoTranscodeJob.State.RETRY_WAIT,
        ]

        ready_missing_hls_qs = Video.objects.filter(
            tenant=tenant,
            status=Video.Status.READY,
        ).filter(Q(hls_path="") | Q(hls_path__isnull=True))
        ready_missing_hls = ready_missing_hls_qs.count()

        ready_missing_thumbnail_qs = Video.objects.filter(
            tenant=tenant,
            status=Video.Status.READY,
            hls_path__gt="",
        ).filter(Q(thumbnail_r2_key="") | Q(thumbnail_r2_key__isnull=True))
        ready_missing_thumbnail = ready_missing_thumbnail_qs.count()

        stale_active_jobs_qs = VideoTranscodeJob.objects.filter(
            tenant=tenant,
            state__in=active_states,
        ).filter(
            (
                Q(state=VideoTranscodeJob.State.RUNNING)
                & (Q(last_heartbeat_at__isnull=True) | Q(last_heartbeat_at__lt=stale_cutoff))
            )
            | (
                Q(state__in=[VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RETRY_WAIT])
                & Q(updated_at__lt=stale_cutoff)
            )
        )
        stale_active_jobs = stale_active_jobs_qs.count()

        mismatched_current_job_qs = Video.objects.filter(
            tenant=tenant,
            current_job__isnull=False,
        ).filter(
            ~Q(current_job__tenant_id=tenant.id) | ~Q(current_job__video_id=F("id"))
        )
        mismatched_current_job = mismatched_current_job_qs.count()

        uploaded_or_processing_without_active_job_qs = Video.objects.filter(
            tenant=tenant,
            status__in=[Video.Status.UPLOADED, Video.Status.PROCESSING],
            updated_at__lt=stale_cutoff,
        ).filter(
            Q(current_job__isnull=True)
            | ~Q(
                current_job__state__in=active_states,
                current_job__tenant_id=tenant.id,
                current_job__video_id=F("id"),
            )
        )
        uploaded_or_processing_without_active_job = uploaded_or_processing_without_active_job_qs.count()

        ready_with_active_current_job_qs = Video.objects.filter(
            tenant=tenant,
            status=Video.Status.READY,
            current_job__state__in=active_states,
        )
        ready_with_active_current_job = ready_with_active_current_job_qs.count()

        recent_dead_jobs_qs = VideoTranscodeJob.objects.filter(
            tenant=tenant,
            state=VideoTranscodeJob.State.DEAD,
            updated_at__gte=now - timedelta(hours=24),
        )
        recent_dead_jobs = recent_dead_jobs_qs.count()

        return [
            CanaryCheck(
                name="video_ready_has_hls",
                severity="error",
                ok=ready_missing_hls == 0,
                detail="READY videos must have an HLS path; otherwise users see a playable item that cannot play.",
                data={
                    "ready_missing_hls": ready_missing_hls,
                    "samples": self._sample_values(
                        ready_missing_hls_qs,
                        ("id", "title", "status", "hls_path", "updated_at"),
                    ),
                },
            ),
            CanaryCheck(
                name="video_active_jobs_not_stale",
                severity="error",
                ok=stale_active_jobs == 0,
                detail="Active transcode jobs must keep making progress.",
                data={
                    "stale_active_jobs": stale_active_jobs,
                    "stale_running_jobs": stale_active_jobs,
                    "stale_minutes": stale_minutes,
                    "samples": self._sample_values(
                        stale_active_jobs_qs,
                        ("id", "video_id", "state", "last_heartbeat_at", "attempt_count", "updated_at"),
                    ),
                },
            ),
            CanaryCheck(
                name="video_current_job_matches_video",
                severity="error",
                ok=mismatched_current_job == 0,
                detail="Video.current_job must belong to the same tenant and video row.",
                data={
                    "mismatched_current_job": mismatched_current_job,
                    "samples": self._sample_values(
                        mismatched_current_job_qs,
                        (
                            "id",
                            "title",
                            "status",
                            "tenant_id",
                            "current_job_id",
                            "current_job__tenant_id",
                            "current_job__video_id",
                            "updated_at",
                        ),
                    ),
                },
            ),
            CanaryCheck(
                name="video_uploaded_processing_has_active_job",
                severity="error",
                ok=uploaded_or_processing_without_active_job == 0,
                detail="Old UPLOADED/PROCESSING videos should have an active transcode job.",
                data={
                    "uploaded_or_processing_without_active_job": uploaded_or_processing_without_active_job,
                    "stale_minutes": stale_minutes,
                    "samples": self._sample_values(
                        uploaded_or_processing_without_active_job_qs,
                        ("id", "title", "status", "current_job_id", "updated_at"),
                    ),
                },
            ),
            CanaryCheck(
                name="video_ready_not_tied_to_active_job",
                severity="warning",
                ok=ready_with_active_current_job == 0,
                detail="READY videos should not still point at an active transcode job.",
                data={
                    "ready_with_active_current_job": ready_with_active_current_job,
                    "samples": self._sample_values(
                        ready_with_active_current_job_qs,
                        ("id", "title", "status", "current_job_id", "updated_at"),
                    ),
                },
            ),
            CanaryCheck(
                name="video_ready_has_thumbnail",
                severity="warning",
                ok=ready_missing_thumbnail == 0,
                detail="READY videos should have thumbnail_r2_key filled by the worker invariant.",
                data={
                    "ready_missing_thumbnail": ready_missing_thumbnail,
                    "samples": self._sample_values(
                        ready_missing_thumbnail_qs,
                        ("id", "title", "status", "hls_path", "thumbnail_r2_key", "updated_at"),
                    ),
                },
            ),
            CanaryCheck(
                name="video_recent_dead_jobs",
                severity="warning",
                ok=recent_dead_jobs == 0,
                detail="Recent DEAD transcode jobs need operational review.",
                data={
                    "recent_dead_jobs": recent_dead_jobs,
                    "window_hours": 24,
                    "samples": self._sample_values(
                        recent_dead_jobs_qs,
                        ("id", "video_id", "state", "error_code", "error_message", "updated_at"),
                    ),
                },
            ),
        ]

    def _check_e2e_residue(self, tenant: Tenant, *, sample_limit: int) -> list[CanaryCheck]:
        residue = self._collect_e2e_residue(tenant, sample_limit=sample_limit)
        total = sum(group["count"] for group in residue.values())
        return [
            CanaryCheck(
                name="production_e2e_residue_absent",
                severity="error",
                ok=total == 0,
                detail="Production tenant must not contain explicit E2E/AUDIT/CHAOS residue rows.",
                data={"total": total, "groups": residue},
            )
        ]

    def _collect_e2e_residue(self, tenant: Tenant, *, sample_limit: int) -> dict[str, dict[str, Any]]:
        from apps.domains.community.models.post import PostEntity
        from apps.domains.exams.models.exam import Exam
        from apps.domains.fees.models import FeeTemplate
        from apps.domains.homework_results.models.homework import Homework
        from apps.domains.matchup.models import MatchupDocument
        from apps.domains.messaging.models import MessageTemplate
        from apps.domains.students.models import Student

        return {
            "students": self._count_residue(
                Student.objects.filter(tenant=tenant),
                fields=("name", "ps_number"),
                sample_limit=sample_limit,
            ),
            "posts": self._count_residue(
                PostEntity.objects.filter(tenant=tenant),
                fields=("title",),
                sample_limit=sample_limit,
            ),
            "matchup_documents": self._count_residue(
                MatchupDocument.objects.filter(tenant=tenant),
                fields=("title",),
                sample_limit=sample_limit,
            ),
            "message_templates": self._count_residue(
                MessageTemplate.objects.filter(tenant=tenant, is_system=False),
                fields=("name",),
                sample_limit=sample_limit,
            ),
            "exams": self._count_residue(
                Exam.objects.filter(tenant=tenant),
                fields=("title",),
                sample_limit=sample_limit,
            ),
            "homeworks": self._count_residue(
                Homework.objects.filter(tenant=tenant),
                fields=("title",),
                sample_limit=sample_limit,
            ),
            "fee_templates": self._count_residue(
                FeeTemplate.objects.filter(tenant=tenant),
                fields=("name",),
                sample_limit=sample_limit,
            ),
        }

    def _count_residue(
        self,
        queryset,
        *,
        fields: Iterable[str],
        sample_limit: int,
    ) -> dict[str, Any]:
        count = 0
        samples: list[dict[str, Any]] = []
        field_list = tuple(fields)
        for obj in queryset.only("id", *field_list).iterator(chunk_size=500):
            values = {field: getattr(obj, field, "") for field in field_list}
            if not any(matches_residue(str(value or "")) for value in values.values()):
                continue
            count += 1
            if len(samples) < sample_limit:
                samples.append({"id": obj.id, **values})
        return {"count": count, "samples": samples}

    def _sample_values(self, queryset, fields: Iterable[str], *, limit: int = 5) -> list[dict[str, Any]]:
        return [dict(row) for row in queryset.values(*fields)[:limit]]

    def _sample_autosend_risks(self, tenant: Tenant, *, limit: int = 5) -> list[dict[str, Any]]:
        from apps.domains.messaging.effective_templates import resolve_effective_template_status
        from apps.domains.messaging.models import AutoSendConfig
        from apps.domains.messaging.policy import get_trigger_implementation_status

        samples: list[dict[str, Any]] = []
        for config in AutoSendConfig.objects.filter(tenant=tenant, enabled=True).select_related("template").order_by("trigger", "id"):
            effective = resolve_effective_template_status(config)
            implementation_status = get_trigger_implementation_status(config.trigger)
            reasons: list[str] = []
            if implementation_status != "implemented":
                reasons.append("not_implemented")
            if not effective.solapi_template_id:
                reasons.append("missing_effective_template")
            elif not effective.is_approved:
                reasons.append("unapproved_effective_template")
            if not reasons:
                continue
            samples.append(
                {
                    "config_id": config.id,
                    "tenant_id": config.tenant_id,
                    "trigger": config.trigger,
                    "template_id": config.template_id,
                    "implementation_status": implementation_status,
                    "effective_source": effective.source,
                    "effective_solapi_template_id": effective.solapi_template_id,
                    "effective_solapi_status": effective.solapi_status,
                    "reasons": reasons,
                }
            )
            if len(samples) >= limit:
                break
        return samples

    def _check_billing(self, tenant: Tenant, *, allow_fee_management_tenant_ids: set[int]) -> list[CanaryCheck]:
        from apps.billing.models import Invoice, PaymentTransaction

        today = timezone.localdate()
        auto_billing_enabled = bool(getattr(settings, "TOSS_AUTO_BILLING_ENABLED", False))
        toss_secret_configured = bool((getattr(settings, "TOSS_PAYMENTS_SECRET_KEY", "") or "").strip())
        toss_client_configured = bool((getattr(settings, "TOSS_PAYMENTS_CLIENT_KEY", "") or "").strip())
        toss_webhook_configured = bool((getattr(settings, "TOSS_WEBHOOK_SECRET", "") or "").strip())
        exempt_ids = set(getattr(settings, "BILLING_EXEMPT_TENANT_IDS", set()) or set())

        program = Program.objects.filter(tenant=tenant).first()
        feature_flags = getattr(program, "feature_flags", None) or {}
        fee_management_enabled = bool(feature_flags.get("fee_management"))
        fee_management_allowed = tenant.id in allow_fee_management_tenant_ids
        live_program_missing_billing_date = False
        if program and tenant.id not in exempt_ids and program.subscription_status in ("active", "grace"):
            live_program_missing_billing_date = not program.subscription_expires_at or not program.next_billing_at

        due_auto_card_invoices = Invoice.objects.filter(
            tenant=tenant,
            billing_mode="AUTO_CARD",
            due_date__lte=today,
            status__in=["SCHEDULED", "PENDING", "FAILED", "OVERDUE"],
        ).count()
        old_pending_transactions = PaymentTransaction.objects.filter(
            tenant=tenant,
            status="PENDING",
            created_at__lt=timezone.now() - timedelta(hours=1),
        ).count()

        return [
            CanaryCheck(
                name="billing_auto_enabled_has_secret",
                severity="error",
                ok=(not auto_billing_enabled) or (toss_secret_configured and toss_webhook_configured),
                detail="Automatic billing must not be enabled without Toss secret and webhook secret.",
                data={
                    "toss_auto_billing_enabled": auto_billing_enabled,
                    "toss_secret_configured": toss_secret_configured,
                    "toss_webhook_configured": toss_webhook_configured,
                    "toss_client_configured": toss_client_configured,
                },
            ),
            CanaryCheck(
                name="fee_management_feature_gate",
                severity="warning",
                ok=(not fee_management_enabled) or fee_management_allowed,
                detail="Fee management is a pre-enable feature and should remain off unless the tenant is explicitly allowlisted.",
                data={
                    "fee_management_enabled": fee_management_enabled,
                    "allowlisted": fee_management_allowed,
                    "allowlist": sorted(allow_fee_management_tenant_ids),
                },
            ),
            CanaryCheck(
                name="billing_live_program_dates",
                severity="warning",
                ok=not live_program_missing_billing_date,
                detail="Live non-exempt programs should have subscription_expires_at and next_billing_at.",
                data={
                    "tenant_exempt": tenant.id in exempt_ids,
                    "program_exists": program is not None,
                    "subscription_status": getattr(program, "subscription_status", ""),
                    "subscription_expires_at": getattr(program, "subscription_expires_at", None),
                    "next_billing_at": getattr(program, "next_billing_at", None),
                },
            ),
            CanaryCheck(
                name="billing_due_auto_card_not_blocked",
                severity="warning",
                ok=auto_billing_enabled or due_auto_card_invoices == 0,
                detail="Due AUTO_CARD invoices exist while automatic billing is disabled.",
                data={
                    "toss_auto_billing_enabled": auto_billing_enabled,
                    "due_auto_card_invoices": due_auto_card_invoices,
                },
            ),
            CanaryCheck(
                name="billing_old_pending_transactions",
                severity="warning",
                ok=old_pending_transactions == 0,
                detail="Payment transactions should not remain pending for more than one hour.",
                data={"old_pending_transactions": old_pending_transactions},
            ),
        ]
