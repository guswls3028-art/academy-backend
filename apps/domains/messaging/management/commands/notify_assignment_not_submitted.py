# apps/support/messaging/management/commands/notify_assignment_not_submitted.py
"""
과제 미제출 알림톡 발송 배치.

사용:
    python manage.py notify_assignment_not_submitted [--dry-run] [--tenant-id N]

동작:
1. session.date가 어제(또는 지정 날짜)인 세션 조회
2. 해당 세션의 HomeworkAssignment 중 HomeworkScore가 없거나 score=None인 학생 추출
3. AutoSendConfig(trigger="assignment_not_submitted")이 enabled인 테넌트만 발송
4. 학부모에게 알림톡 발송

스케줄러(cron/EventBridge)로 매일 1회 실행 권장.
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.support.messaging.assignment_not_submitted_dependencies import (
    assignments_for_homework_session,
    first_attempt_homework_is_not_submitted,
    homeworks_for_session,
    sessions_for_assignment_not_submitted,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "과제 미제출 학생에게 알림톡 발송 (배치)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="실제 발송 없이 대상 확인만",
        )
        parser.add_argument(
            "--tenant-id",
            type=int,
            default=None,
            help="특정 테넌트만 처리 (미지정 시 전체)",
        )
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="대상 세션 날짜 (YYYY-MM-DD, 미지정 시 어제)",
        )

    def handle(self, *args, **options):
        from apps.core.models import Tenant
        from apps.domains.messaging.selectors import get_auto_send_config
        from apps.domains.messaging.services import send_event_notification
        from apps.domains.messaging.policy import get_owner_tenant_id

        dry_run = options["dry_run"]
        tenant_id_filter = options.get("tenant_id")

        # 대상 날짜 결정
        if options.get("date"):
            from datetime import date as date_type
            target_date = date_type.fromisoformat(options["date"])
        else:
            target_date = (timezone.localtime() - timedelta(days=1)).date()

        self.stdout.write(f"Target date: {target_date} (dry_run={dry_run})")

        # 대상 세션 조회
        sessions = sessions_for_assignment_not_submitted(
            target_date=target_date,
            tenant_id=tenant_id_filter,
        )
        if not sessions:
            self.stdout.write("No sessions found for target date.")
            return

        self.stdout.write(f"Found {len(sessions)} session(s)")

        sent_count = 0
        skip_count = 0
        owner_id = get_owner_tenant_id()

        for session in sessions:
            tenant_id = session.lecture.tenant_id
            tenant = session.lecture.tenant if hasattr(session.lecture, "tenant") else None
            if not tenant:
                try:
                    tenant = Tenant.objects.get(pk=tenant_id)
                except Tenant.DoesNotExist:
                    continue

            # AutoSendConfig 확인
            config = get_auto_send_config(tenant_id, "assignment_not_submitted")
            if not config and tenant_id != owner_id:
                config = get_auto_send_config(owner_id, "assignment_not_submitted")
            if not config or not config.enabled:
                continue

            # 해당 세션의 과제 목록
            homeworks = homeworks_for_session(session)
            if not homeworks:
                continue

            for homework in homeworks:
                # 과제 대상자
                assignments = assignments_for_homework_session(
                    homework=homework,
                    session=session,
                )

                for assignment in assignments:
                    enrollment = assignment.enrollment
                    student = enrollment.student if enrollment else None
                    if not student:
                        continue
                    if getattr(student, "deleted_at", None):
                        continue
                    if enrollment.status != "ACTIVE":
                        continue

                    is_not_submitted = first_attempt_homework_is_not_submitted(
                        homework=homework,
                        enrollment=enrollment,
                    )
                    if not is_not_submitted:
                        skip_count += 1
                        continue

                    if dry_run:
                        self.stdout.write(
                            f"  [DRY] tenant={tenant_id} student={student.name} "
                            f"homework={homework.title} session={session.title}"
                        )
                        sent_count += 1
                        continue

                    try:
                        ok = send_event_notification(
                            tenant=tenant,
                            trigger="assignment_not_submitted",
                            student=student,
                            send_to="parent",
                            context={
                                "강의명": session.lecture.title or "",
                                "차시명": session.title or session.display_label,
                                "과제명": homework.title or "",
                                "_domain_object_id": f"hw_{homework.id}_s{student.id}",
                            },
                        )
                        if ok:
                            sent_count += 1
                        else:
                            skip_count += 1
                    except Exception:
                        logger.exception(
                            "assignment_not_submitted notification failed: "
                            "tenant=%s student=%s homework=%s",
                            tenant_id, student.id, homework.id,
                        )
                        skip_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. sent={sent_count} skipped={skip_count} (dry_run={dry_run})"
            )
        )
