# PATH: apps/domains/results/views/admin_exam_item_score_view.py
"""
Admin Manual Grading - Subjective Question Score

PATCH /results/admin/exams/{exam_id}/enrollments/{enrollment_id}/items/{question_id}/

✅ PHASE 2 핵심 패치
- PATCH 성공 시 backend는 즉시:
  - (이미 수행) ResultItem + ResultFact + Result.total_score 재계산
  - (추가) progress pipeline을 즉시 트리거한다.
    -> SessionProgress / LectureProgress / Risk / ClinicLink 최신화

⚠️ progress pipeline은 submission_id 기반이므로,
- 해당 enrollment + (exam에 연결된 대표 session) 기준 최신 Submission을 찾아 트리거한다.
- Submission이 없으면 즉시성 계약을 지킬 수 없으므로 409 반환한다.
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

# ✅ 단일 진실: session 매핑 + progress 트리거
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.submissions.models import Submission
from apps.domains.progress.dispatcher import dispatch_progress_pipeline


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
        # 2️⃣ Attempt 상태 확인 (LOCK)
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
        agg = ResultItem.objects.filter(result=result)

        total_score = sum(float(x.score or 0.0) for x in agg)
        max_total = sum(float(x.max_score or 0.0) for x in agg)

        result.total_score = float(total_score)
        result.max_score = float(max_total)
        result.save(update_fields=["total_score", "max_score"])

        # -------------------------------------------------
        # 7️⃣ ✅ PHASE 2: progress pipeline 즉시 트리거
        # -------------------------------------------------
        session = get_primary_session_for_exam(exam_id)
        if not session:
            return Response(
                {"detail": "session not found for this exam; cannot recalculate progress", "code": "INVALID"},
                status=drf_status.HTTP_409_CONFLICT,
            )

        submission = (
            Submission.objects
            .filter(enrollment_id=enrollment_id, session_id=int(session.id))
            .order_by("-id")
            .first()
        )
        if not submission:
            return Response(
                {"detail": "no submission found; cannot recalculate progress", "code": "NO_SUBMISSION"},
                status=drf_status.HTTP_409_CONFLICT,
            )

        dispatch_progress_pipeline(int(submission.id))

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
