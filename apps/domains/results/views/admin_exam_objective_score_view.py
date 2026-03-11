# PATH: apps/domains/results/views/admin_exam_objective_score_view.py
"""
PATCH /results/admin/exams/{exam_id}/enrollments/{enrollment_id}/objective/

객관식 점수만 입력. total_score = objective_score + sum(ResultItem) 로 동기화.
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


class AdminExamObjectiveScoreView(APIView):
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
            new_objective = float(request.data.get("score"))
        except Exception:
            raise ValidationError({"detail": "score must be number", "code": "INVALID"})

        if new_objective < 0:
            raise ValidationError({"detail": "score must be >= 0", "code": "INVALID"})

        max_score = 100.0
        if new_objective > max_score:
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
                result.objective_score = 0.0
                result.save(update_fields=["attempt_id", "max_score", "objective_score", "updated_at"])

        attempt = ExamAttempt.objects.filter(id=int(result.attempt_id)).first()
        if not attempt:
            raise NotFound({"detail": "attempt not found", "code": "NOT_FOUND"})
        if attempt.status == "grading":
            return Response(
                {"detail": "attempt is grading", "code": "LOCKED"},
                status=drf_status.HTTP_409_CONFLICT,
            )

        subjective_sum = sum(
            float(x.score or 0.0)
            for x in ResultItem.objects.filter(result=result)
        )
        new_total = new_objective + subjective_sum
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
            question_id=0,
            answer="",
            is_correct=bool(float(new_total) >= float(pass_score)),
            score=float(new_total),
            max_score=float(result.max_score or max_score),
            source="manual_objective",
            meta={
                "manual_objective": True,
                "objective_score": new_objective,
                "edited_at": timezone.now().isoformat(),
            },
        )

        result.objective_score = float(new_objective)
        result.total_score = float(new_total)
        result.max_score = float(result.max_score or max_score)
        result.save(update_fields=["objective_score", "total_score", "max_score", "updated_at"])

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
                "total_score": float(result.total_score or 0.0),
                "max_score": float(result.max_score or 0.0),
            },
            status=drf_status.HTTP_200_OK,
        )
