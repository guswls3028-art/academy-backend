# apps/domains/results/views/admin_representative_attempt_view.py
from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import ExamAttempt, Result


class AdminRepresentativeAttemptView(APIView):
    """
    POST /results/admin/exams/<exam_id>/representative-attempt/

    요청:
    {
      "enrollment_id": 55,
      "attempt_id": 1234
    }

    동작:
    1) (exam_id, enrollment_id) 의 모든 attempt → is_representative=False
    2) 지정 attempt → is_representative=True
    3) Result.attempt_id도 해당 attempt로 동기화

    ✅ 운영/CS 필수 API
    - 재시험 실패
    - 채점 오류
    - 대표 점수 수동 교체
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def post(self, request, exam_id: int):
        enrollment_id = request.data.get("enrollment_id")
        attempt_id = request.data.get("attempt_id")

        if not enrollment_id or not attempt_id:
            raise ValidationError("enrollment_id and attempt_id are required")

        exam_id = int(exam_id)
        enrollment_id = int(enrollment_id)
        attempt_id = int(attempt_id)

        # 1️⃣ attempt 검증
        target = ExamAttempt.objects.filter(
            id=attempt_id,
            exam_id=exam_id,
            enrollment_id=enrollment_id,
        ).first()

        if not target:
            raise ValidationError("attempt not found for this exam/enrollment")

        # 2️⃣ 대표 attempt 초기화
        ExamAttempt.objects.filter(
            exam_id=exam_id,
            enrollment_id=enrollment_id,
        ).update(is_representative=False)

        # 3️⃣ 지정 attempt 대표로 설정
        target.is_representative = True
        target.save(update_fields=["is_representative"])

        # 4️⃣ Result 스냅샷도 동기화
        Result.objects.filter(
            target_type="exam",
            target_id=exam_id,
            enrollment_id=enrollment_id,
        ).update(attempt_id=attempt_id)

        return Response({
            "ok": True,
            "exam_id": exam_id,
            "enrollment_id": enrollment_id,
            "attempt_id": attempt_id,
        })
