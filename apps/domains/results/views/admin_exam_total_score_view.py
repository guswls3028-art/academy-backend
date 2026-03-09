from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status as drf_status
from rest_framework.exceptions import ValidationError, NotFound

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import Result, ResultFact, ExamAttempt

from apps.domains.exams.models import Exam

from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.submissions.models import Submission
from apps.domains.progress.dispatcher import dispatch_progress_pipeline


class AdminExamTotalScoreView(APIView):
    """
    PATCH /results/admin/exams/{exam_id}/enrollments/{enrollment_id}/score/

    ✅ 목적
    - 성적 탭에서 시험 "합산 점수"를 직접 입력 (Result.total_score override)

    ⚠️ 주의
    - ResultItem 합과 total_score가 불일치할 수 있다.
      (문항별 채점 모드와 합산 입력 모드가 동시에 사용될 수 있으므로, 모드 선택은 프론트 UX로 제어)
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    @transaction.atomic
    def patch(self, request, exam_id: int, enrollment_id: int):
        exam_id = int(exam_id)
        enrollment_id = int(enrollment_id)

        if "score" not in request.data:
            raise ValidationError({"detail": "score is required", "code": "INVALID"})

        try:
            new_score = float(request.data.get("score"))
        except Exception:
            raise ValidationError({"detail": "score must be number", "code": "INVALID"})

        if new_score < 0:
            raise ValidationError({"detail": "score must be >= 0", "code": "INVALID"})

        max_score = None
        if "max_score" in request.data:
            raw_max = request.data.get("max_score", None)
            if raw_max is not None:
                try:
                    max_score = float(raw_max)
                except Exception:
                    raise ValidationError({"detail": "max_score must be number", "code": "INVALID"})
                if max_score < 0:
                    raise ValidationError({"detail": "max_score must be >= 0", "code": "INVALID"})

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
            raise ValidationError({"detail": "representative attempt not set", "code": "INVALID"})

        # -------------------------------------------------
        # 2️⃣ Attempt LOCK 상태 확인
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
        # 3️⃣ 점수 범위 검증
        # -------------------------------------------------
        effective_max = max_score if max_score is not None else float(result.max_score or 0.0)
        if effective_max and new_score > float(effective_max):
            raise ValidationError(
                {"detail": f"score must be between 0 and {effective_max}", "code": "INVALID"}
            )

        # -------------------------------------------------
        # 4️⃣ ResultFact (append-only 로그)
        # -------------------------------------------------
        exam = Exam.objects.filter(id=exam_id).first()
        pass_score = float(getattr(exam, "pass_score", 0.0) or 0.0) if exam else 0.0

        # submission은 있을 수도/없을 수도 있음 (오프라인 입력 허용)
        submission_id = 0
        session = get_primary_session_for_exam(exam_id)
        if session:
            submission = (
                Submission.objects
                .filter(enrollment_id=enrollment_id, session_id=int(session.id))
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
            question_id=0,  # total override marker
            answer="",
            is_correct=bool(float(new_score) >= float(pass_score)),
            score=float(new_score),
            max_score=float(effective_max or 0.0),
            source="manual_total",
            meta={
                "manual_total": True,
                "edited_at": timezone.now().isoformat(),
            },
        )

        # -------------------------------------------------
        # 5️⃣ Result 업데이트
        # -------------------------------------------------
        result.total_score = float(new_score)
        if max_score is not None:
            result.max_score = float(max_score)
            result.save(update_fields=["total_score", "max_score", "updated_at"])
        else:
            result.save(update_fields=["total_score", "updated_at"])

        # -------------------------------------------------
        # 6️⃣ progress pipeline (best-effort)
        # -------------------------------------------------
        if submission_id:
            def _dispatch():
                dispatch_progress_pipeline(int(submission_id))
            transaction.on_commit(_dispatch)

        return Response(
            {
                "ok": True,
                "exam_id": exam_id,
                "enrollment_id": enrollment_id,
                "total_score": float(result.total_score or 0.0),
                "max_score": float(result.max_score or 0.0),
                "progress": {"dispatched": bool(submission_id), "reason": None if submission_id else "NO_SUBMISSION"},
            },
            status=drf_status.HTTP_200_OK,
        )

