# PATH: apps/domains/results/views/admin_representative_attempt_view.py
"""
Admin Representative Attempt Switch

POST /results/admin/exams/<exam_id>/representative-attempt/

✅ PHASE 2 핵심 패치 (정합성)
- 대표 attempt 변경 시 Result.attempt_id만 바꾸면 스냅샷 불일치 가능
- 따라서:
  1) 대표 attempt 교체
  2) ResultFact(attempt_id=선택 attempt) 기반으로 Result/ResultItem 스냅샷을 즉시 재구성
  3) progress pipeline 즉시 트리거

LOCK 규칙은 기존 유지:
- grading attempt는 대표로 변경 불가 (409 LOCKED)
"""

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
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    @staticmethod
    def _rebuild_result_snapshot_from_attempt(
        *,
        exam_id: int,
        enrollment_id: int,
        attempt_id: int,
    ) -> Result:
        """
        ✅ 대표 attempt 기반으로 Result/ResultItem 스냅샷을 재구성한다.

        규칙:
        - ResultFact는 append-only라 같은 question_id가 여러 번 존재 가능
        - question_id별 최신 Fact(id 최대)만 스냅샷으로 채택
        """
        result = (
            Result.objects
            .select_for_update()
            .filter(target_type="exam", target_id=exam_id, enrollment_id=enrollment_id)
            .first()
        )
        if not result:
            raise NotFound({"detail": "result snapshot not found", "code": "NOT_FOUND"})

        # question_id별 최신 fact id
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

        # 스냅샷 비어있으면(해당 attempt에 fact가 없음) invalid
        if not facts:
            raise ValidationError({"detail": "no facts for this attempt; cannot rebuild snapshot", "code": "INVALID"})

        # ResultItem 재구성
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

        # Result 스냅샷 갱신
        result.attempt_id = int(attempt_id)
        result.total_score = float(total)
        result.max_score = float(max_total)
        result.submitted_at = timezone.now()
        result.save(update_fields=["attempt_id", "total_score", "max_score", "submitted_at", "updated_at"])

        return result

    @transaction.atomic
    def post(self, request, exam_id: int):
        exam_id = int(exam_id)

        enrollment_id = request.data.get("enrollment_id")
        attempt_id = request.data.get("attempt_id")

        if enrollment_id is None or attempt_id is None:
            raise ValidationError({"detail": "enrollment_id and attempt_id are required", "code": "INVALID"})

        enrollment_id = int(enrollment_id)
        attempt_id = int(attempt_id)

        # -------------------------------------------------
        # 1) 범위 잠금 + 대상 attempt 검증
        # -------------------------------------------------
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

        # -------------------------------------------------
        # 2) LOCKED 정책
        # -------------------------------------------------
        if (target.status or "").lower() == "grading":
            return Response(
                {"detail": "attempt is grading; cannot switch representative", "code": "LOCKED"},
                status=drf_status.HTTP_409_CONFLICT,
            )

        # -------------------------------------------------
        # 3) 대표 attempt 교체 (DB invariant)
        # -------------------------------------------------
        attempts_qs.filter(is_representative=True).update(is_representative=False)
        if not target.is_representative:
            target.is_representative = True
            target.save(update_fields=["is_representative"])

        # -------------------------------------------------
        # 4) ✅ PHASE 2: 스냅샷 재빌드 (정합성)
        # -------------------------------------------------
        self._rebuild_result_snapshot_from_attempt(
            exam_id=exam_id,
            enrollment_id=enrollment_id,
            attempt_id=attempt_id,
        )

        # -------------------------------------------------
        # 5) ✅ PHASE 2: progress pipeline 즉시 트리거
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
                "attempt_id": attempt_id,
            },
            status=drf_status.HTTP_200_OK,
        )
