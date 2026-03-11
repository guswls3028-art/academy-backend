# PATH: apps/domains/results/views/admin_exam_subjective_score_view.py
"""
PATCH /results/admin/exams/{exam_id}/enrollments/{enrollment_id}/subjective/

주관식 점수(합계)만 입력. total_score = objective_score + subjective_score 로 동기화.
- ResultItem은 question_id=0 인 단일 항목(주관식 합계)으로 유지하여
  sum(ResultItem) === subjective_score, total === objective + subjective 보장.
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
from apps.domains.exams.models import Exam
from apps.domains.submissions.models import Submission
from apps.domains.progress.dispatcher import dispatch_progress_pipeline
from django.db.models import Max

# 주관식 합계용 플레이스홀더(문항별가 아닌 한 칸 입력 시)
SUBJECTIVE_AGGREGATE_QUESTION_ID = 0


class AdminExamSubjectiveScoreView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    @transaction.atomic
    def patch(self, request, exam_id: int, enrollment_id: int):
        exam_id = int(exam_id)
        enrollment_id = int(enrollment_id)

        # ✅ tenant isolation: verify exam belongs to tenant
        from django.shortcuts import get_object_or_404 as _get_or_404
        _get_or_404(Exam, id=exam_id, sessions__lecture__tenant=request.tenant)

        if "score" not in request.data:
            raise ValidationError({"detail": "score is required", "code": "INVALID"})

        try:
            new_subjective = float(request.data.get("score"))
        except Exception:
            raise ValidationError({"detail": "score must be number", "code": "INVALID"})

        if new_subjective < 0:
            raise ValidationError({"detail": "score must be >= 0", "code": "INVALID"})

        max_score = 100.0
        if new_subjective > max_score:
            raise ValidationError(
                {"detail": f"score must be between 0 and {max_score}", "code": "INVALID"}
            )

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
        if not result or not result.attempt_id:
            qs = (
                ExamAttempt.objects
                .select_for_update()
                .filter(exam_id=exam_id, enrollment_id=enrollment_id)
            )
            last = qs.aggregate(Max("attempt_index")).get("attempt_index__max") or 0
            next_index = int(last) + 1
            qs.filter(is_representative=True).update(is_representative=False)
            attempt = ExamAttempt.objects.create(
                exam_id=exam_id,
                enrollment_id=enrollment_id,
                submission_id=0,
                attempt_index=next_index,
                is_retake=(last > 0),
                is_representative=True,
                status="done",
            )
            if not result:
                result = Result.objects.create(
                    target_type="exam",
                    target_id=exam_id,
                    enrollment_id=enrollment_id,
                    attempt_id=int(attempt.id),
                    total_score=0.0,
                    max_score=float(max_score),
                    objective_score=0.0,
                )
            else:
                result.attempt_id = int(attempt.id)
                result.max_score = float(max_score)
                result.save(update_fields=["attempt_id", "max_score", "updated_at"])

        attempt = ExamAttempt.objects.filter(id=int(result.attempt_id)).first()
        if not attempt:
            raise NotFound({"detail": "attempt not found", "code": "NOT_FOUND"})
        if attempt.status == "grading":
            return Response(
                {"detail": "attempt is grading", "code": "LOCKED"},
                status=drf_status.HTTP_409_CONFLICT,
            )

        objective = float(getattr(result, "objective_score", 0.0) or 0.0)
        new_total = objective + new_subjective
        exam = Exam.objects.filter(id=exam_id).first()
        pass_score = float(getattr(exam, "pass_score", 0.0) or 0.0) if exam else 0.0

        submission_id = 0
        submission = (
            Submission.objects
            .filter(
                enrollment_id=enrollment_id,
                target_type=Submission.TargetType.EXAM,
                target_id=exam_id,
            )
            .order_by("-id")
            .first()
        )
        if submission:
            submission_id = int(submission.id)

        ResultFact.objects.create(
            target_type="exam",
            target_id=exam_id,
            enrollment_id=enrollment_id,
            submission_id=submission_id,
            attempt_id=int(result.attempt_id),
            question_id=SUBJECTIVE_AGGREGATE_QUESTION_ID,
            answer="",
            is_correct=bool(float(new_total) >= float(pass_score)),
            score=float(new_subjective),
            max_score=float(max_score),
            source="manual_subjective",
            meta={
                "manual_subjective": True,
                "subjective_score": new_subjective,
                "edited_at": timezone.now().isoformat(),
            },
        )

        # 주관식 합계 한 칸 입력: question_id=0 단일 ResultItem으로 유지 (total = objective + subjective 보장)
        ResultItem.objects.filter(result=result).delete()
        ResultItem.objects.create(
            result=result,
            question_id=SUBJECTIVE_AGGREGATE_QUESTION_ID,
            answer="",
            is_correct=bool(new_subjective >= max_score),
            score=float(new_subjective),
            max_score=float(max_score),
            source="manual_subjective",
        )

        result.total_score = float(new_total)
        result.max_score = float(max_score)
        result.save(update_fields=["total_score", "max_score", "updated_at"])

        if submission_id:
            def _dispatch():
                dispatch_progress_pipeline(int(submission_id))
            transaction.on_commit(_dispatch)

        return Response(
            {
                "ok": True,
                "exam_id": exam_id,
                "enrollment_id": enrollment_id,
                "objective_score": float(result.objective_score or 0.0),
                "subjective_score": float(new_subjective),
                "total_score": float(result.total_score or 0.0),
                "max_score": float(result.max_score or 0.0),
            },
            status=drf_status.HTTP_200_OK,
        )
