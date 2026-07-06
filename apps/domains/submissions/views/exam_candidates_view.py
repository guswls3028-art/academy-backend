# PATH: apps/domains/submissions/views/exam_candidates_view.py
"""
OMR 검토 학생 picker 전용 엔드포인트.

GET /api/v1/submissions/submissions/exams/<exam_id>/candidates/?q=<query>

- 해당 시험의 ExamEnrollment을 기반으로 응시 대상 학생 리스트 반환.
- ExamEnrollment이 비어있으면 exam.sessions의 SessionEnrollment로 fallback.
- q (검색어): 학생명, 학생폰 뒤 8자리, 학부모폰 뒤 8자리 부분일치.
- 최대 50건.
- 응답 필드: enrollment_id, student_name, student_phone, parent_phone,
             lecture_title, lecture_color, lecture_chip_label,
             already_matched (기존 submission과 매칭된 학생인지).
"""
from __future__ import annotations

from typing import Set

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.submissions.models import Submission
from apps.support.submissions.candidate_dependencies import exam_candidate_rows


class ExamCandidatesView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, exam_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([], status=200)

        candidates = exam_candidate_rows(
            tenant=tenant,
            exam_id=int(exam_id),
            q=str(request.query_params.get("q") or "").strip(),
        )
        if not candidates.found:
            return Response({"detail": "시험을 찾을 수 없습니다."}, status=404)

        # 이미 submission 매칭된 enrollment_id
        matched_ids: Set[int] = set(
            Submission.objects
            .filter(
                tenant=tenant,
                target_type=Submission.TargetType.EXAM,
                target_id=int(exam_id),
                enrollment_id__isnull=False,
            )
            .exclude(enrollment_id=0)
            .values_list("enrollment_id", flat=True)
        )

        items = []
        for row in candidates.rows:
            item = dict(row)
            item["already_matched"] = int(item["enrollment_id"]) in matched_ids
            items.append(item)

        return Response(items, status=200)
