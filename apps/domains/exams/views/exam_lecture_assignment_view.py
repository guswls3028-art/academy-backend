from __future__ import annotations

from collections import defaultdict
import math

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.exams.models import Exam, ExamEnrollment, ExamLecturePolicy
from apps.domains.exams.services.lecture_policy_service import (
    effective_pass_scores_for_exam,
)
from apps.support.exams.view_dependencies import (
    active_enrollment_ids_for_exam_assignment,
    available_session_for_exam_assignment,
    dispatch_progress_for_exam,
    linked_lecture_for_exam_assignment,
    session_roster_rows_for_exam_assignment,
)


def _validate_pass_score(*, exam: Exam, raw_value) -> float:
    try:
        pass_score = float(raw_value)
    except (TypeError, ValueError, OverflowError):
        raise ValidationError({"pass_score": "숫자로 입력해 주세요."})
    if not math.isfinite(pass_score):
        raise ValidationError({"pass_score": "유한한 숫자로 입력해 주세요."})
    if pass_score < 0:
        raise ValidationError({"pass_score": "0 이상이어야 합니다."})
    if pass_score > float(exam.max_score or 0.0):
        raise ValidationError(
            {"pass_score": f"만점({float(exam.max_score or 0.0):g})을 초과할 수 없습니다."}
        )
    return pass_score


def build_exam_lecture_assignments(*, exam: Exam, tenant) -> dict:
    sessions = list(
        exam.sessions.filter(
            lecture__tenant=tenant,
            lecture__is_system=False,
        )
        .select_related("lecture", "section")
        .order_by("lecture__display_order", "lecture_id", "order", "id")
    )
    session_ids = [int(session.id) for session in sessions]
    roster_rows = session_roster_rows_for_exam_assignment(
        tenant=tenant,
        session_ids=session_ids,
    )
    enrollment_ids_by_session: dict[int, set[int]] = defaultdict(set)
    for row in roster_rows:
        enrollment_ids_by_session[int(row["session_id"])].add(
            int(row["enrollment_id"])
        )

    selected_ids = set(
        ExamEnrollment.objects.filter(
            exam=exam,
            enrollment__tenant=tenant,
        ).values_list("enrollment_id", flat=True)
    )
    lecture_ids = {int(session.lecture_id) for session in sessions}
    pass_scores = effective_pass_scores_for_exam(
        exam=exam,
        lecture_ids=lecture_ids,
    )
    explicit_policy_ids = set(
        ExamLecturePolicy.objects.filter(
            exam=exam,
            lecture_id__in=lecture_ids,
        ).values_list("lecture_id", flat=True)
    )

    grouped: dict[int, dict] = {}
    total_roster_ids: set[int] = set()
    for session in sessions:
        lecture_id = int(session.lecture_id)
        roster_ids = enrollment_ids_by_session.get(int(session.id), set())
        total_roster_ids.update(roster_ids)
        group = grouped.setdefault(
            lecture_id,
            {
                "lecture_id": lecture_id,
                "lecture_title": session.lecture.title,
                "lecture_color": session.lecture.color,
                "lecture_chip_label": session.lecture.chip_label,
                "pass_score": pass_scores[lecture_id],
                "uses_default_pass_score": lecture_id not in explicit_policy_ids,
                "roster_ids": set(),
                "sessions": [],
            },
        )
        group["roster_ids"].update(roster_ids)
        group["sessions"].append(
            {
                "session_id": int(session.id),
                "session_title": session.title,
                "session_label": session.display_label,
                "session_date": session.date,
                "section_label": (
                    session.section.label if session.section_id is not None else None
                ),
            }
        )

    assignments = []
    for group in grouped.values():
        roster_ids = group.pop("roster_ids")
        group["roster_count"] = len(roster_ids)
        group["selected_count"] = len(roster_ids & selected_ids)
        assignments.append(group)

    return {
        "exam_id": int(exam.id),
        "default_pass_score": float(exam.pass_score or 0.0),
        "total_roster_count": len(total_roster_ids),
        "total_selected_count": len(total_roster_ids & selected_ids),
        "assignments": assignments,
    }


class ExamLectureAssignmentView(APIView):
    permission_classes = [TenantResolvedAndStaff]

    def _exam(self, request, exam_id: int, *, for_update: bool = False) -> Exam:
        queryset = Exam.objects.filter(
            tenant=request.tenant,
            exam_type=Exam.ExamType.REGULAR,
            is_active=True,
        )
        if for_update:
            queryset = queryset.select_for_update()
        return get_object_or_404(queryset, pk=exam_id)

    def get(self, request, exam_id: int):
        exam = self._exam(request, exam_id)
        return Response(
            build_exam_lecture_assignments(exam=exam, tenant=request.tenant)
        )

    @transaction.atomic
    def post(self, request, exam_id: int):
        exam = self._exam(request, exam_id, for_update=True)
        raw_session_id = request.data.get("session_id")
        try:
            session_id = int(raw_session_id)
        except (TypeError, ValueError):
            raise ValidationError({"session_id": "올바른 차시를 선택해 주세요."})

        session = available_session_for_exam_assignment(
            tenant=request.tenant,
            session_id=session_id,
        )
        if session is None:
            raise ValidationError({"session_id": "이 학원에서 사용할 수 없는 차시입니다."})

        was_linked = exam.sessions.filter(id=session_id).exists()
        if not was_linked:
            exam.sessions.add(session)

        if "pass_score" in request.data:
            pass_score = _validate_pass_score(
                exam=exam,
                raw_value=request.data.get("pass_score"),
            )
            ExamLecturePolicy.objects.update_or_create(
                exam=exam,
                lecture=session.lecture,
                defaults={"pass_score": pass_score},
            )

        active_ids = active_enrollment_ids_for_exam_assignment(
            tenant=request.tenant,
            session=session,
        )
        ExamEnrollment.objects.bulk_create(
            [
                ExamEnrollment(exam=exam, enrollment_id=enrollment_id)
                for enrollment_id in active_ids
            ],
            ignore_conflicts=True,
        )
        transaction.on_commit(lambda: dispatch_progress_for_exam(exam_id=int(exam.id)))
        payload = build_exam_lecture_assignments(
            exam=exam,
            tenant=request.tenant,
        )
        return Response(
            payload,
            status=status.HTTP_200_OK if was_linked else status.HTTP_201_CREATED,
        )

    @transaction.atomic
    def patch(self, request, exam_id: int):
        exam = self._exam(request, exam_id, for_update=True)
        try:
            lecture_id = int(request.data.get("lecture_id"))
        except (TypeError, ValueError):
            raise ValidationError({"lecture_id": "올바른 강의를 선택해 주세요."})
        lecture = linked_lecture_for_exam_assignment(
            tenant=request.tenant,
            exam=exam,
            lecture_id=lecture_id,
        )
        if lecture is None:
            raise ValidationError({"lecture_id": "이 시험에 연결되지 않은 강의입니다."})
        pass_score = _validate_pass_score(
            exam=exam,
            raw_value=request.data.get("pass_score"),
        )
        ExamLecturePolicy.objects.update_or_create(
            exam=exam,
            lecture=lecture,
            defaults={"pass_score": pass_score},
        )
        transaction.on_commit(lambda: dispatch_progress_for_exam(exam_id=int(exam.id)))
        return Response(
            build_exam_lecture_assignments(exam=exam, tenant=request.tenant)
        )
