# PATH: apps/domains/submissions/views/homework_candidates_view.py
"""
Homework 검토 학생 picker 전용 엔드포인트.

GET /api/v1/submissions/submissions/homework/<homework_id>/candidates/?q=<query>

- 해당 과제의 SessionEnrollment 기반 학생 리스트.
- q (검색어): 학생명, 학생폰 뒤 8자리, 학부모폰 뒤 8자리 부분일치.
- 최대 50건.
- 응답 필드: enrollment_id, student_name, student_phone_last4,
             parent_phone_last4, lecture_title, lecture_color,
             lecture_chip_label, already_matched.

Tenant isolation: homework.tenant 강제.
"""
from __future__ import annotations

from typing import Set

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.submissions.models import Submission
from apps.support.submissions.candidate_dependencies import homework_candidate_rows


class HomeworkCandidatesView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, homework_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([], status=200)

        candidates = homework_candidate_rows(
            tenant=tenant,
            homework_id=int(homework_id),
            q=str(request.query_params.get("q") or "").strip(),
        )
        if not candidates.found:
            return Response({"detail": "과제를 찾을 수 없습니다."}, status=404)

        # 이미 submission 매칭된 enrollment_id
        matched_ids: Set[int] = set(
            Submission.objects
            .filter(
                tenant=tenant,
                target_type=Submission.TargetType.HOMEWORK,
                target_id=int(homework_id),
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
