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

        # ✅ tenant isolation: verify exam belongs to tenant
        from django.shortcuts import get_object_or_404 as _get_or_404
        _get_or_404(Exam, id=exam_id, sessions__lecture__tenant=request.tenant)

        if "score" not in request.data:
            raise ValidationError({"detail": "score is required", "code": "INVALID"})

        try:
            new_score = float(request.data.get("score"))
        except Exception:
            raise ValidationError({"detail": "score must be number", "code": "INVALID"})

        if new_score < 0:
            raise ValidationError({"detail": "score must be >= 0", "code": "INVALID"})

        # 성적 탭 입력은 0~100 고정 (요구사항)
        max_score = 100.0

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
        if not result or not result.attempt_id:
            # 과제 quick patch처럼 "없으면 생성" (수동 입력용 attempt/result 생성)
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
        effective_max = float(max_score)
        if new_score > float(effective_max):
            raise ValidationError(
                {"detail": f"score must be between 0 and {effective_max}", "code": "INVALID"}
            )

        # -------------------------------------------------
        # 4️⃣ ResultFact (append-only 로그)
        # -------------------------------------------------
        exam = Exam.objects.filter(id=exam_id).first()
        pass_score = float(getattr(exam, "pass_score", 0.0) or 0.0) if exam else 0.0

        # submission은 있을 수도/없을 수도 있음 (오프라인 입력 허용)
        # Submission 모델에는 session_id 없음 → exam+enrollment 기준으로 최신 제출 조회
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
            question_id=0,  # total override marker
            answer="",
            is_correct=bool(float(new_score) >= float(pass_score)),
            score=float(new_score),
            max_score=float(effective_max),
            source="manual_total",
            meta={
                "manual_total": True,
                "edited_at": timezone.now().isoformat(),
            },
        )

        # -------------------------------------------------
        # 5️⃣ Result 업데이트 (합산 입력 시 total만 변경, objective_score 유지)
        # -------------------------------------------------
        result.total_score = float(new_score)
        result.max_score = float(max_score)
        result.save(update_fields=["total_score", "max_score", "updated_at"])

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

