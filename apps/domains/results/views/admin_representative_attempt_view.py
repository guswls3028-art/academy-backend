# PATH: apps/domains/results/views/admin_representative_attempt_view.py
# (동작 변경 없음: 이미 스냅샷 재빌드 + progress 트리거 포함)
# 아래 파일은 PHASE 7 종료 기준 문서만 보강하고 로직은 그대로 둔다.

from __future__ import annotations

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status as drf_status
from rest_framework.exceptions import ValidationError, NotFound

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import ExamAttempt, Result, ResultItem, ResultFact

# ✅ 단일 진실: session 매핑 + progress 트리거
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.submissions.models import Submission
from apps.domains.progress.dispatcher import dispatch_progress_pipeline


class AdminRepresentativeAttemptView(APIView):
    """
    POST /results/admin/exams/<exam_id>/representative-attempt/

    ✅ PHASE 7 기준 (고정)
    - 대표 attempt 변경은 "is_representative"만 바꾸는 행위가 아니다.
    - Result 스냅샷(Result/ResultItem)은 선택된 attempt의 Fact(append-only)에서 즉시 재구성한다.
    - 이후 progress pipeline을 즉시 트리거하여 파생 결과를 최신화한다.

    🚫 금지
    - 모델/마이그레이션 유발 변경
    - 프론트 계약 변경
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    @staticmethod
    def _rebuild_result_snapshot_from_attempt(
        *,
        exam_id: int,
        enrollment_id: int,
        attempt_id: int,
    ) -> Result:
        result = (
            Result.objects
            .select_for_update()
            .filter(target_type="exam", target_id=exam_id, enrollment_id=enrollment_id)
            .first()
        )
        if not result:
            raise NotFound({"detail": "result snapshot not found", "code": "NOT_FOUND"})

        latest_fact_ids = (
            ResultFact.objects
            .filter(
                target_type="exam",
                target_id=exam_id,
                enrollment_id=enrollment_id,
                attempt_id=attempt_id,
            )
            .values("question_id")
            .annotate(last_id=Max("id"))
            .values("last_id")
        )

        facts = list(ResultFact.objects.filter(id__in=latest_fact_ids))
        if not facts:
            raise ValidationError({"detail": "no facts for this attempt; cannot rebuild snapshot", "code": "INVALID"})

        total = 0.0
        max_total = 0.0

        for f in facts:
            score = float(f.score or 0.0)
            max_score = float(f.max_score or 0.0)

            ResultItem.objects.update_or_create(
                result=result,
                question_id=int(f.question_id),
                defaults={
                    "answer": str(f.answer or ""),
                    "is_correct": bool(f.is_correct),
                    "score": score,
                    "max_score": max_score,
                    "source": str(f.source or ""),
                },
            )
            total += score
            max_total += max_score

        result.attempt_id = int(attempt_id)
        result.total_score = float(total)
        result.max_score = float(max_total)
        result.submitted_at = timezone.now()
        result.save(update_fields=["attempt_id", "total_score", "max_score", "submitted_at", "updated_at"])

        return result

    @transaction.atomic
    def post(self, request, exam_id: int):
        exam_id = int(exam_id)

        # ✅ tenant isolation: verify exam belongs to tenant
        from apps.domains.exams.models import Exam
        from django.shortcuts import get_object_or_404
        get_object_or_404(Exam, id=exam_id, sessions__lecture__tenant=request.tenant)

        enrollment_id = request.data.get("enrollment_id")
        attempt_id = request.data.get("attempt_id")

        if enrollment_id is None or attempt_id is None:
            raise ValidationError({"detail": "enrollment_id and attempt_id are required", "code": "INVALID"})

        enrollment_id = int(enrollment_id)
        attempt_id = int(attempt_id)

        attempts_qs = (
            ExamAttempt.objects
            .select_for_update()
            .filter(exam_id=exam_id, enrollment_id=enrollment_id)
        )

        if not attempts_qs.exists():
            raise NotFound({"detail": "attempts not found for this exam/enrollment", "code": "NOT_FOUND"})

        target = attempts_qs.filter(id=attempt_id).first()
        if not target:
            raise NotFound({"detail": "attempt not found for this exam/enrollment", "code": "NOT_FOUND"})

        if (target.status or "").lower() == "grading":
            return Response(
                {"detail": "attempt is grading; cannot switch representative", "code": "LOCKED"},
                status=drf_status.HTTP_409_CONFLICT,
            )

        attempts_qs.filter(is_representative=True).update(is_representative=False)
        if not target.is_representative:
            target.is_representative = True
            target.save(update_fields=["is_representative"])

        self._rebuild_result_snapshot_from_attempt(
            exam_id=exam_id,
            enrollment_id=enrollment_id,
            attempt_id=attempt_id,
        )

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
                "attempt_id": attempt_id,
            },
            status=drf_status.HTTP_200_OK,
        )
