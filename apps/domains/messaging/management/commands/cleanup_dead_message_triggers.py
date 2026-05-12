"""Dead 알림톡 trigger AutoSendConfig row cleanup (2026-05-12).

audit 식별 결과 (`project_backlog_2026_05_12_evening.md`):
  - `clinic_check_out` — `clinic_self_study_completed` 로 통합되며 SSOT 제거
  - `urgent_notice` — 카카오 알림톡 정책 위반으로 제거
  - `class_enrollment_complete` / `enrollment_expiring_soon` / `student_signup` — DISABLED

위 5종 trigger 의 AutoSendConfig row 일괄 삭제. tenant 격리 절대.

사용:
  python manage.py cleanup_dead_message_triggers --dry-run    # 미리보기
  python manage.py cleanup_dead_message_triggers --force      # 확인 없이 실행
  python manage.py cleanup_dead_message_triggers              # 대화형 확인
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.domains.messaging.models import AutoSendConfig


DEAD_TRIGGERS = [
    "clinic_check_out",            # → clinic_self_study_completed 통합
    "urgent_notice",                # 카카오 정책 위반 제거
    "class_enrollment_complete",    # DISABLED
    "enrollment_expiring_soon",     # DISABLED (미구현)
    "student_signup",               # DISABLED (레거시)
]


class Command(BaseCommand):
    help = "Dead 알림톡 trigger AutoSendConfig row cleanup"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="실제 삭제 안 하고 영향 받는 row 만 list",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="대화형 확인 없이 즉시 삭제",
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]

        rows = AutoSendConfig.objects.filter(trigger__in=DEAD_TRIGGERS).order_by("trigger", "tenant_id")
        total = rows.count()

        breakdown: dict[str, list[tuple]] = {}
        for c in rows:
            breakdown.setdefault(c.trigger, []).append((c.tenant_id, c.enabled))

        self.stdout.write(self.style.MIGRATE_HEADING(f"\nDead trigger AutoSendConfig audit"))
        self.stdout.write(f"  총 {total} row, {len(breakdown)} trigger")
        for t in DEAD_TRIGGERS:
            list_ = breakdown.get(t, [])
            if not list_:
                self.stdout.write(f"  - {t}: (none)")
                continue
            enabled = sum(1 for _, e in list_ if e)
            self.stdout.write(f"  - {t}: {len(list_)} rows (enabled={enabled})")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("\n삭제할 row 없음. 작업 완료."))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING(f"\n--dry-run: 실제 삭제 X. {total} row 가 삭제 대상."))
            return

        if not options["force"]:
            try:
                confirm = input(f"\n{total} row 삭제 진행? (yes/N): ").strip().lower()
            except EOFError:
                confirm = ""
            if confirm != "yes":
                self.stdout.write(self.style.WARNING("취소됨."))
                return

        with transaction.atomic():
            deleted, _ = rows.delete()

        self.stdout.write(self.style.SUCCESS(f"\n삭제 완료: {deleted} row"))
