# PATH: apps/support/messaging/management/commands/clinic_messaging_verify.py
"""
운영 환경 클리닉 자동 메시징 검증: 설정 감사(audit-config) 및 발송 로그 점검(check-logs).

워커가 SQS 페이로드의 event_type을 NotificationLog.notification_type에 기록한다(신규 메시지부터).
과거 로그는 notification_type이 비어 있을 수 있다.

사용 예:
  python manage.py clinic_messaging_verify audit-config --tenant-id=3
  python manage.py clinic_messaging_verify check-logs --tenant-id=3 --minutes=90
  python manage.py clinic_messaging_verify check-logs --tenant-id=3 --minutes=120 --expect-success=clinic_reservation_created,clinic_check_in
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

# policy.TRIGGER_POLICY 와 동기화 (clinic_* 전부)
CLINIC_MESSAGING_TRIGGERS: tuple[str, ...] = (
    "clinic_reservation_created",
    "clinic_reservation_changed",
    "clinic_cancelled",
    "clinic_check_in",
    "clinic_check_out",
    "clinic_absent",
    "clinic_reminder",
    "clinic_self_study_completed",
    "clinic_result_notification",
)

# 코드상 미구현: 실제 발송 검증 불가
CLINIC_NOT_IMPLEMENTED: frozenset[str] = frozenset({"clinic_reminder"})


class Command(BaseCommand):
    help = "Clinic auto messaging: audit-config or check-logs (see docs/02-OPERATIONS/clinic-messaging-production-verification.md)."

    def add_arguments(self, parser):
        parser.add_argument(
            "mode",
            choices=["audit-config", "check-logs"],
            help="audit-config or check-logs",
        )
        parser.add_argument("--tenant-id", type=int, required=True, dest="tenant_id")
        parser.add_argument(
            "--minutes",
            type=int,
            default=60,
            help="check-logs: time window in minutes (default 60)",
        )
        parser.add_argument(
            "--expect-success",
            type=str,
            default="",
            help="check-logs: comma-separated triggers that must appear as successful clinic_* logs or exit 1",
        )

    def handle(self, *args, **options):
        mode = options["mode"]
        tenant_id = options["tenant_id"]
        if mode == "audit-config":
            self._audit_config(tenant_id)
        else:
            self._check_logs(
                tenant_id,
                minutes=options["minutes"],
                expect_raw=options["expect_success"] or "",
            )

    def _audit_config(self, tenant_id: int) -> None:
        from apps.domains.messaging.models import AutoSendConfig
        from django.conf import settings

        owner_id = int(getattr(settings, "OWNER_TENANT_ID", 1))

        self.stdout.write(f"=== clinic_* AutoSendConfig (tenant_id={tenant_id}) ===")
        for trigger in CLINIC_MESSAGING_TRIGGERS:
            if trigger in CLINIC_NOT_IMPLEMENTED:
                self.stdout.write(f"  {trigger}: (코드 미구현: 세션 send_reminder 501)")
                continue
            row = (
                AutoSendConfig.objects.filter(tenant_id=tenant_id, trigger=trigger)
                .select_related("template")
                .first()
            )
            if not row:
                self.stdout.write(
                    self.style.WARNING(
                        f"  {trigger}: 테넌트에 행 없음, send_event_notification 시 오너(tenant {owner_id}) 설정 폴백 가능"
                    )
                )
                continue
            tpl = row.template
            approved = bool(
                tpl
                and (tpl.solapi_template_id or "").strip()
                and getattr(tpl, "solapi_status", "") == "APPROVED"
            )
            mode = (row.message_mode or "alimtalk").strip()
            self.stdout.write(
                f"  {trigger}: enabled={row.enabled} mode={mode} template_linked={bool(tpl)} "
                f"alimtalk_approved={approved}"
            )

    def _check_logs(self, tenant_id: int, *, minutes: int, expect_raw: str) -> None:
        from apps.domains.messaging.models import NotificationLog

        since = timezone.now() - timedelta(minutes=minutes)
        qs = NotificationLog.objects.filter(
            tenant_id=tenant_id,
            sent_at__gte=since,
        ).order_by("-sent_at")

        self.stdout.write(
            f"=== NotificationLog tenant={tenant_id} since={since.isoformat()} ({minutes}m) ==="
        )

        clinic_ok = set(
            qs.filter(success=True, notification_type__startswith="clinic_")
            .values_list("notification_type", flat=True)
            .distinct()
        )
        self.stdout.write("clinic_* 성공 트리거(중복 제거): " + (", ".join(sorted(clinic_ok)) or "(없음)"))

        recent_fail = qs.filter(success=False, notification_type__startswith="clinic_")[:15]
        if recent_fail.exists():
            self.stdout.write(self.style.WARNING("--- 최근 clinic_* 실패 샘플 ---"))
            for r in recent_fail:
                self.stdout.write(
                    f"  {r.sent_at.isoformat()} {r.notification_type!r} {r.failure_reason[:80]!r}"
                )

        if not expect_raw.strip():
            return

        expected = {t.strip() for t in expect_raw.split(",") if t.strip()}
        missing = expected - clinic_ok
        if missing:
            raise CommandError(
                f"expect-success 미충족: 다음 트리거의 성공 로그가 없음: {sorted(missing)}. "
                f"(워커·SQS·수신번호·자동발송 설정을 확인. 과거 로그는 notification_type 비어 있을 수 있음)"
            )
        self.stdout.write(self.style.SUCCESS(f"expect-success 충족: {sorted(expected)}"))
