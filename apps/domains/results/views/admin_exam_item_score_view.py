# ==========================================================================================
# FILE: apps/domains/results/views/admin_exam_item_score_view.py
# ==========================================================================================
"""
Admin Manual Grading - Subjective Question Score

PATCH /results/admin/exams/{exam_id}/enrollments/{enrollment_id}/items/{question_id}/

==========================================================================================
✅ 목적 (Phase 3)
==========================================================================================
- 주관식/수동 채점 점수를 문항 단위로 수정한다.
- 항상 "대표 attempt" 기준 Result 스냅샷을 수정한다.
- ResultItem + ResultFact를 동시에 반영한다.
- total_score는 즉시 재계산된다.

==========================================================================================
✅ 프론트 계약 (고정)
==========================================================================================
- 저장 성공 후:
    → 반드시 AdminExamResultDetail 재조회
- 이 API는 "저장"만 책임진다.
  (clinic_required / pass 여부 판단은 detail API가 진실의 원천)

==========================================================================================
⚠️ 주의
==========================================================================================
- grading 중인 attempt는 수정 불가 (LOCKED)
- 점수 상한(max_score) 초과는 허용하지 않는다.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status as drf_status
from rest_framework.exceptions import ValidationError, NotFound

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import Result, ResultItem, ResultFact, ExamAttempt


class AdminExamItemScoreView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    @transaction.atomic
    def patch(
        self,
        request,
        exam_id: int,
        enrollment_id: int,
        question_id: int,
    ):
        exam_id = int(exam_id)
        enrollment_id = int(enrollment_id)
        question_id = int(question_id)

        if "score" not in request.data:
            raise ValidationError({"detail": "score is required", "code": "INVALID"})

        try:
            new_score = float(request.data.get("score"))
        except Exception:
            raise ValidationError({"detail": "score must be number", "code": "INVALID"})

        # -------------------------------------------------
        # 1️⃣ Result (대표 스냅샷)
        # -------------------------------------------------
        result = (
            Result.objects
            .select_for_update()
            .filter(
                target_type="exam",
                target_id=exam_id,
                enrollment_id=enrollment_id,
            )
            .first()
        )
        if not result:
            raise NotFound({"detail": "result not found", "code": "NOT_FOUND"})

        if not result.attempt_id:
            raise ValidationError(
                {"detail": "representative attempt not set", "code": "INVALID"}
            )

        # -------------------------------------------------
        # 2️⃣ Attempt 상태 확인
        # -------------------------------------------------
        attempt = ExamAttempt.objects.filter(id=int(result.attempt_id)).first()
        if not attempt:
            raise NotFound({"detail": "attempt not found", "code": "NOT_FOUND"})

        if attempt.status == "grading":
            return Response(
                {"detail": "attempt is grading", "code": "LOCKED"},
                status=drf_status.HTTP_409_CONFLICT,
            )

        # -------------------------------------------------
        # 3️⃣ ResultItem (문항 스냅샷)
        # -------------------------------------------------
        item = (
            ResultItem.objects
            .select_for_update()
            .filter(result=result, question_id=question_id)
            .first()
        )
        if not item:
            raise NotFound({"detail": "result item not found", "code": "NOT_FOUND"})

        # 점수 상한 방어
        max_score = float(item.max_score or 0.0)
        if new_score < 0 or new_score > max_score:
            raise ValidationError(
                {
                    "detail": f"score must be between 0 and {max_score}",
                    "code": "INVALID",
                }
            )

        # -------------------------------------------------
        # 4️⃣ ResultFact (append-only 로그)
        # -------------------------------------------------
        ResultFact.objects.create(
            target_type="exam",
            target_id=exam_id,
            enrollment_id=enrollment_id,
            submission_id=0,              # 수동 채점이므로 0
            attempt_id=int(result.attempt_id),

            question_id=question_id,
            answer=item.answer or "",
            is_correct=bool(new_score >= max_score),
            score=float(new_score),
            max_score=max_score,
            source="manual",
            meta={
                "manual": True,
                "edited_at": timezone.now().isoformat(),
            },
        )

        # -------------------------------------------------
        # 5️⃣ ResultItem 업데이트
        # -------------------------------------------------
        item.score = float(new_score)
        item.is_correct = bool(new_score >= max_score)
        item.source = "manual"
        item.save(update_fields=["score", "is_correct", "source"])

        # -------------------------------------------------
        # 6️⃣ total_score 재계산
        # -------------------------------------------------
        agg = (
            ResultItem.objects
            .filter(result=result)
        )

        total_score = sum(float(x.score or 0.0) for x in agg)
        max_total = sum(float(x.max_score or 0.0) for x in agg)

        result.total_score = float(total_score)
        result.max_score = float(max_total)
        result.save(update_fields=["total_score", "max_score"])

        return Response(
            {
                "ok": True,
                "exam_id": exam_id,
                "enrollment_id": enrollment_id,
                "question_id": question_id,
                "score": float(new_score),
                "total_score": float(total_score),
                "max_score": float(max_total),
            },
            status=drf_status.HTTP_200_OK,
        )
