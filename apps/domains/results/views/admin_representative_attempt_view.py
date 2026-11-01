from __future__ import annotations

import logging
import math

from django.db import transaction
from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status as drf_status
from rest_framework.exceptions import ValidationError, NotFound

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import ExamAttempt, Result
from apps.domains.results.services.attempt_snapshot import (
    apply_result_snapshot,
    resolve_result_snapshot_from_attempt,
)
from apps.domains.results.guards.score_edit_lease_guard import (
    require_score_edit_scope_available_for_exam,
)

from apps.domains.results.utils.session_exam import (
    get_unambiguous_session_for_exam_lecture,
)
from apps.support.results.admin_exam_dependencies import (
    dispatch_progress_pipeline,
    get_enrollment_for_tenant,
    get_latest_exam_submission_id,
    lock_regular_active_exam_for_tenant,
)

logger = logging.getLogger(__name__)


def _score_float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError, OverflowError):
        return math.nan


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
    def _resolve_result_snapshot_from_attempt(**kwargs):
        try:
            return resolve_result_snapshot_from_attempt(**kwargs)
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict) from exc

    _apply_result_snapshot = staticmethod(apply_result_snapshot)

    @transaction.atomic
    def post(self, request, exam_id: int):
        exam_id = int(exam_id)

        # ✅ tenant isolation: verify exam belongs to tenant
        exam = lock_regular_active_exam_for_tenant(
            exam_id=exam_id,
            tenant=request.tenant,
        )
        require_score_edit_scope_available_for_exam(
            exam=exam,
            tenant=request.tenant,
        )

        enrollment_id = request.data.get("enrollment_id")
        attempt_id = request.data.get("attempt_id")

        if enrollment_id is None or attempt_id is None:
            raise ValidationError({"detail": "enrollment_id and attempt_id are required", "code": "INVALID"})

        enrollment_id = int(enrollment_id)
        attempt_id = int(attempt_id)
        enrollment = get_enrollment_for_tenant(
            enrollment_id=enrollment_id,
            tenant=request.tenant,
        )
        if enrollment is None:
            raise NotFound({"detail": "enrollment not found", "code": "NOT_FOUND"})
        session = get_unambiguous_session_for_exam_lecture(
            exam_id=exam_id,
            lecture_id=getattr(enrollment, "lecture_id", None),
        )
        if session is None:
            return Response(
                {
                    "detail": "one exact session is required for this exam and lecture",
                    "code": "INVALID",
                },
                status=drf_status.HTTP_409_CONFLICT,
            )

        result = (
            Result.objects.select_for_update()
            .filter(
                target_type="exam",
                target_id=exam_id,
                enrollment_id=enrollment_id,
            )
            .first()
        )
        if not result:
            raise NotFound(
                {"detail": "result snapshot not found", "code": "NOT_FOUND"}
            )

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

        question_facts, total_score, objective_score, submitted_at = (
            self._resolve_result_snapshot_from_attempt(
                result=result,
                attempt=target,
                exam=exam,
            )
        )
        current_max_score = float(exam.max_score or 100.0)
        if (
            not math.isfinite(total_score)
            or total_score < 0
            or total_score > current_max_score
            or not math.isfinite(objective_score)
            or objective_score < 0
            or objective_score > current_max_score
            or objective_score > total_score
            or any(
                not math.isfinite(_score_float(fact.score))
                or not math.isfinite(_score_float(fact.max_score))
                or _score_float(fact.score) < 0
                or _score_float(fact.max_score) < 0
                or _score_float(fact.score) > _score_float(fact.max_score)
                for fact in question_facts
            )
        ):
            raise ValidationError(
                {
                    "detail": (
                        "selected attempt score must be finite and between "
                        f"0 and {current_max_score}"
                    ),
                    "code": "INVALID",
                }
            )

        attempts_qs.filter(is_representative=True).update(is_representative=False)
        if not target.is_representative:
            target.is_representative = True
            target.save(update_fields=["is_representative"])

        self._apply_result_snapshot(
            result=result,
            attempt_id=attempt_id,
            question_facts=question_facts,
            total_score=total_score,
            objective_score=objective_score,
            current_max_score=current_max_score,
            submitted_at=submitted_at,
        )

        submission_id = get_latest_exam_submission_id(
            enrollment_id=enrollment_id,
            exam_id=exam_id,
        )
        def _dispatch_progress() -> None:
            try:
                if submission_id:
                    dispatch_progress_pipeline(submission_id=int(submission_id))
                else:
                    dispatch_progress_pipeline(exam_id=exam_id)
            except Exception:
                logger.exception(
                    "representative progress dispatch failed (exam=%s, submission=%s)",
                    exam_id,
                    submission_id,
                )

        transaction.on_commit(_dispatch_progress)

        return Response(
            {
                "ok": True,
                "exam_id": exam_id,
                "enrollment_id": enrollment_id,
                "attempt_id": attempt_id,
            },
            status=drf_status.HTTP_200_OK,
        )
