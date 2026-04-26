"""
Auto-close overdue exams and homeworks for sessions whose next session date has arrived.

Background:
  현재 어드민 사이드패널([SessionAssessmentSidePanel.tsx])이 마운트될 때마다
  useEffect 로 "다음 차시 날짜가 지난 OPEN 시험/과제" 를 닫는 로직을 수행해 왔다.
  → 어드민이 그 페이지를 열어야만 닫히므로, 안 열면 영원히 OPEN 으로 남는 운영 위험.

이 커맨드:
  - 모든 테넌트의 강의 단위로 차시를 정렬
  - (curr, next) 쌍에서 today >= next.date 인 경우 curr 의 OPEN 시험/과제를 CLOSED 처리
  - 마지막 차시는 today >= curr.date + 1 day 인 경우 CLOSED 처리

Usage:
    python manage.py close_overdue_assessments [--dry-run]

EventBridge 스케줄: backend/infra/terraform/purge_schedule.tf (close_overdue_assessments_daily)
"""
from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.domains.exams.models import Exam
from apps.domains.homework_results.models import Homework
from apps.domains.lectures.models import Lecture, Session


def _walk_session_pairs(sessions: Iterable[Session]):
    """
    sorted Session iterable → yield (curr, next or None) pairs.
    """
    sorted_sessions = sorted(sessions, key=lambda s: (s.order or 0, s.id))
    for i, s in enumerate(sorted_sessions):
        nxt = sorted_sessions[i + 1] if i + 1 < len(sorted_sessions) else None
        yield s, nxt


def _deadline_for(curr: Session, nxt: Session | None):
    """
    Returns the date at/after which curr's OPEN assessments should be closed.
    - 다음 차시가 있으면: 다음 차시의 date
    - 없으면: curr.date + 1 day
    - curr.date 가 없으면 None (마감 판정 불가)
    """
    if nxt is not None and nxt.date:
        return nxt.date
    if curr.date:
        return curr.date + timedelta(days=1)
    return None


class Command(BaseCommand):
    help = "Auto-close exams/homeworks whose owning session is overdue (next session arrived)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be closed without writing to the DB.",
        )

    def handle(self, *args, **options):
        dry = bool(options.get("dry_run"))
        today = timezone.localdate()

        total_exams_closed = 0
        total_hws_closed = 0
        sessions_touched = 0

        # 강의 단위로 차시를 묶어 (curr, next) 페어를 만든다.
        # tenant 격리는 Lecture.tenant 가 자연 보장.
        lectures = Lecture.objects.all().only("id", "tenant_id").iterator(chunk_size=200)

        for lecture in lectures:
            sessions = list(
                Session.objects.filter(lecture_id=lecture.id).only("id", "order", "date")
            )
            if not sessions:
                continue

            session_ids_to_close: list[int] = []
            for curr, nxt in _walk_session_pairs(sessions):
                deadline = _deadline_for(curr, nxt)
                if deadline is None:
                    continue
                if today >= deadline:
                    session_ids_to_close.append(curr.id)

            if not session_ids_to_close:
                continue

            # 시험/과제 OPEN → CLOSED (한 번에)
            exam_qs = Exam.objects.filter(
                sessions__id__in=session_ids_to_close,
                status=Exam.Status.OPEN,
            ).distinct()
            hw_qs = Homework.objects.filter(
                session_id__in=session_ids_to_close,
                status=Homework.Status.OPEN,
            )

            exam_count = exam_qs.count()
            hw_count = hw_qs.count()

            if exam_count == 0 and hw_count == 0:
                continue

            sessions_touched += len(session_ids_to_close)
            total_exams_closed += exam_count
            total_hws_closed += hw_count

            self.stdout.write(
                f"  lecture={lecture.id} tenant={lecture.tenant_id} "
                f"sessions={session_ids_to_close} exams={exam_count} homeworks={hw_count}"
            )

            if not dry:
                with transaction.atomic():
                    if exam_count:
                        exam_qs.update(status=Exam.Status.CLOSED)
                    if hw_count:
                        hw_qs.update(status=Homework.Status.CLOSED)

        prefix = "[DRY-RUN] " if dry else ""
        self.stdout.write(
            f"{prefix}done. sessions_with_overdue={sessions_touched} "
            f"exams_closed={total_exams_closed} homeworks_closed={total_hws_closed}"
        )
