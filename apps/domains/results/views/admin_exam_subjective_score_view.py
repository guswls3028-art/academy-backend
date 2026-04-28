# PATH: apps/domains/results/views/admin_exam_subjective_score_view.py
"""
PATCH /results/admin/exams/{exam_id}/enrollments/{enrollment_id}/subjective/

주관식 점수(합계)만 입력. total_score = objective_score + subjective_score 로 동기화.
- ResultItem은 question_id=0 인 단일 항목(주관식 합계)으로 유지하여
  sum(ResultItem) === subjective_score, total === objective + subjective 보장.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

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

        # ✅ tenant isolation: verify enrollment belongs to tenant
        from apps.domains.results.guards.enrollment_tenant_guard import validate_enrollment_belongs_to_tenant
        validate_enrollment_belongs_to_tenant(enrollment_id, request.tenant)

        if "score" not in request.data:
            raise ValidationError({"detail": "score is required", "code": "INVALID"})

        try:
            new_subjective = float(request.data.get("score"))
        except Exception:
            raise ValidationError({"detail": "score must be number", "code": "INVALID"})

        if new_subjective < 0:
            raise ValidationError({"detail": "score must be >= 0", "code": "INVALID"})

        exam = Exam.objects.filter(id=exam_id).first()
        max_score = float(getattr(exam, "max_score", 100.0) or 100.0)
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
            # attempt_index=1: 1차 점수 스냅샷. Subjective는 이 시점 객관식=0이므로
            # total_score = new_subjective (1차 기준 고정).
            _initial_meta = None
            if next_index == 1:
                _initial_meta = {
                    "initial_snapshot": {
                        "total_score": float(new_subjective),
                        "max_score": float(max_score),
                        "submitted_at": timezone.now().isoformat(),
                        "source": "admin_manual_subjective",
                    }
                }
            attempt = ExamAttempt.objects.create(
                exam_id=exam_id,
                enrollment_id=enrollment_id,
                submission_id=0,
                attempt_index=next_index,
                is_retake=(last > 0),
                is_representative=True,
                status="done",
                meta=_initial_meta,
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

        _sid = int(submission_id) if submission_id else 0
        _eid = int(exam_id)
        def _dispatch_progress():
            try:
                if _sid:
                    dispatch_progress_pipeline(submission_id=_sid)
                else:
                    dispatch_progress_pipeline(exam_id=_eid)
            except Exception:
                logger.exception("progress pipeline dispatch failed (exam=%s, submission=%s)", _eid, _sid)
        transaction.on_commit(_dispatch_progress)

        # 성적 공개 알림톡 (best-effort)
        _new_total = float(result.total_score or 0.0)
        _max = float(result.max_score or max_score)
        _exam_title = str(getattr(exam, "title", "") or "")
        _enrollment_id = enrollment_id
        _tenant = request.tenant
        def _send_score_notification():
            try:
                from apps.domains.enrollment.models import Enrollment as _Enr
                from apps.domains.messaging.services import send_event_notification
                enr = _Enr.objects.select_related("student", "lecture").filter(
                    id=_enrollment_id, tenant=_tenant
                ).first()
                if enr and enr.student:
                    # 차시명: exam → sessions에서 첫 번째 session title
                    _session_title = ""
                    try:
                        from apps.domains.exams.models import Exam as _Exam
                        _first_session = _Exam.objects.filter(id=_eid).values_list(
                            "sessions__title", flat=True
                        ).first()
                        _session_title = str(_first_session or "")
                    except Exception:
                        pass
                    send_event_notification(
                        tenant=_tenant,
                        trigger="exam_score_published",
                        student=enr.student,
                        send_to="parent",
                        context={
                            "시험명": _exam_title,
                            "강의명": str(getattr(enr.lecture, "title", "") or ""),
                            "차시명": _session_title,
                            "시험성적": f"{int(_new_total)}/{int(_max)}",
                            "_domain_object_id": str(_eid),
                        },
                    )
            except Exception:
                logger.debug("exam_score_published notification failed", exc_info=True)
        transaction.on_commit(_send_score_notification)

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
