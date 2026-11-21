# PATH: apps/domains/submissions/views/exam_candidates_view.py
"""
OMR 검토 학생 picker 전용 엔드포인트.

GET /api/v1/submissions/submissions/exams/<exam_id>/candidates/?q=<query>

- 해당 시험의 ExamEnrollment을 기반으로 응시 대상 학생 리스트 반환.
- ExamEnrollment이 비어있으면 exam.sessions의 SessionEnrollment로 fallback.
- session_id: 해당 시험과 연결된 차시로 제한하며, 응답에 검증된 차시 ID를 포함한다.
- q (검색어): 학생명, 학생·학부모 전화번호 일부 또는 정확한 뒤 8자리.
- unmatched_only=1: 이미 활성 답안지가 있는 학생을 제외하고 50건까지 반환.
- 최대 50건.
- 응답 필드: enrollment_id, student_name, student_phone, parent_phone,
             lecture_title, lecture_color, lecture_chip_label,
             already_matched (기존 submission과 매칭된 학생인지).
"""
from __future__ import annotations

from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.common.query_params import parse_query_bool, parse_query_int
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.lifecycle import OMR_CONFLICT_STATUSES
from apps.support.submissions.candidate_dependencies import exam_candidate_rows


class ExamCandidatesView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, exam_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([], status=200)

        session_id = parse_query_int(request.query_params, "session_id", min_value=1, max_value=2**63 - 1)
        if "session_id" in request.query_params and (
            session_id is None or len(request.query_params.getlist("session_id")) != 1
        ):
            raise ValidationError({"session_id": "차시 ID를 하나 입력해 주세요."})

        unmatched_only = parse_query_bool(request.query_params, "unmatched_only", default=False)
        matched = Submission.objects.filter(
            tenant=tenant, target_type=Submission.TargetType.EXAM, target_id=exam_id,
            status__in=OMR_CONFLICT_STATUSES, enrollment_id__isnull=False,
        ).order_by()
        candidates = exam_candidate_rows(
            tenant=tenant,
            exam_id=int(exam_id),
            q=str(request.query_params.get("q") or "").strip(),
            session_id=session_id,
            exclude_enrollment_ids=matched.values("enrollment_id") if unmatched_only else None,
        )
        if not candidates.found:
            return Response({"detail": "시험을 찾을 수 없습니다."}, status=404)
        if not candidates.session_valid:
            raise ValidationError({"session_id": "해당 시험에 연결된 차시가 아닙니다."})

        matched_ids = set()
        if not unmatched_only:
            displayed_ids = [row["enrollment_id"] for row in candidates.rows]
            matched_ids = set(matched.filter(enrollment_id__in=displayed_ids)
                              .values_list("enrollment_id", flat=True).distinct())
        items = [{**row, "already_matched": row["enrollment_id"] in matched_ids} for row in candidates.rows]

        return Response(
            items if session_id is None else {"session_id": session_id, "candidates": items},
            status=200,
        )
