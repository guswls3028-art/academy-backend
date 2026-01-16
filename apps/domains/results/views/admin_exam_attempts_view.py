# ==========================================================================================
# FILE: apps/domains/results/views/admin_exam_attempts_view.py
# ==========================================================================================
"""
Admin Exam Attempt List View

GET /results/admin/exams/{exam_id}/enrollments/{enrollment_id}/attempts/

==========================================================================================
✅ 목적 (Phase 1)
==========================================================================================
- Admin / Teacher가 특정 시험(exam_id) + 특정 enrollment의
  ExamAttempt 목록을 조회한다.
- AttemptSelectorPanel의 데이터 소스

==========================================================================================
✅ 계약 (프론트 고정)
==========================================================================================
응답:
[
  {
    "id": 101,
    "attempt_index": 1,
    "is_retake": false,
    "is_representative": true,
    "status": "done",
    "created_at": "2025-01-01T10:00:00Z",
    "meta": {
      "grading": {
        "total_score": 85,
        "total_max_score": 100
      }
    }
  }
]

- 정렬: attempt_index ASC
- 대표 attempt: 항상 1개 보장 (서버 invariant)
- status enum:
    pending | grading | done | failed

==========================================================================================
⚠️ 주의
==========================================================================================
- 수정/대표 변경 ❌ (Phase 2에서 구현)
- enrollment_id는 Enrollment PK 기준 (results 도메인 전체 계약과 동일)
"""

from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import ExamAttempt


class AdminExamAttemptsView(APIView):
    """
    Admin / Teacher 전용 Attempt 목록 조회
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int, enrollment_id: int):
        exam_id = int(exam_id)
        enrollment_id = int(enrollment_id)

        # -------------------------------------------------
        # 1️⃣ Attempt 조회
        # -------------------------------------------------
        attempts = (
            ExamAttempt.objects
            .filter(
                exam_id=exam_id,
                enrollment_id=enrollment_id,
            )
            .order_by("attempt_index")  # ✅ 프론트 계약
        )

        if not attempts.exists():
            # 빈 배열을 내려도 되지만,
            # Admin 화면에서는 보통 "존재하지 않음"이 의미 있는 오류라 판단
            raise ValidationError("No attempts found for this exam/enrollment.")

        # -------------------------------------------------
        # 2️⃣ 응답 구성 (Serializer 없이 명시적 dict)
        #    - 프론트 계약 안정성
        #    - meta 구조를 그대로 노출 가능
        # -------------------------------------------------
        data = []
        for a in attempts:
            row = {
                "id": a.id,
                "attempt_index": a.attempt_index,
                "is_retake": bool(a.is_retake),
                "is_representative": bool(a.is_representative),
                "status": a.status,
                "created_at": a.created_at,
            }

            # meta 필드가 있으면 그대로 노출 (grading 정보 포함 가능)
            if hasattr(a, "meta") and a.meta is not None:
                row["meta"] = a.meta
            else:
                row["meta"] = {}

            data.append(row)

        return Response(data)
