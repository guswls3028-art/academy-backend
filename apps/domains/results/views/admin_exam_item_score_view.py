# PATH: apps/domains/results/views/admin_exam_item_score_view.py
# (동작 변경 없음: 이미 progress 트리거 포함)
# 아래 파일은 "PHASE 7 종료 기준" 문서만 보강하고 로직은 그대로 둔다.

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
from apps.domains.exams.models import ExamQuestion

# ✅ 단일 진실: session 매핑 + progress 트리거
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.submissions.models import Submission
from apps.domains.progress.dispatcher import dispatch_progress_pipeline


class AdminExamItemScoreView(APIView):
    """
    PATCH /results/admin/exams/{exam_id}/enrollments/{enrollment_id}/items/{question_id}/

    ✅ PHASE 7 기준 (고정)
    - 수동 채점은 ResultFact(append-only) + ResultItem 스냅샷 갱신으로 기록한다.
    - total_score/max_score는 ResultItem 합으로 재계산한다.
    - 변경 즉시 progress pipeline을 트리거하여 SessionProgress/ClinicLink 등 파생 결과를 최신화한다.

    🚫 금지
    - 모델/마이그레이션 유발 변경
    - 프론트 계약 변경
    """

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

        # ✅ tenant isolation: verify exam belongs to tenant
        from apps.domains.exams.models import Exam
        from django.shortcuts import get_object_or_404
        get_object_or_404(Exam, id=exam_id, sessions__lecture__tenant=request.tenant)

        # ✅ tenant isolation: verify enrollment belongs to tenant
        from apps.domains.results.guards.enrollment_tenant_guard import validate_enrollment_belongs_to_tenant
        validate_enrollment_belongs_to_tenant(enrollment_id, request.tenant)

        if "score" not in request.data:
            raise ValidationError({"detail": "score is required", "code": "INVALID"})

        try:
            new_score = float(request.data.get("score"))
        except Exception:
            raise ValidationError({"detail": "score must be number", "code": "INVALID"})

        # ✅ 답안 필드 (수동 입력용, 선택 사항)
        new_answer = request.data.get("answer")  # None이면 미변경

        # -------------------------------------------------
        # 1️⃣ Result (대표 스냅샷) — 없으면 자동 생성 (수동 입력용)
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
            from apps.domains.enrollment.models import Enrollment
            enrollment_obj = Enrollment.objects.filter(
                id=enrollment_id, tenant=request.tenant
            ).first()
            if not enrollment_obj:
                raise NotFound({"detail": "enrollment not found", "code": "NOT_FOUND"})

            from apps.domains.exams.models import Exam
            exam_obj = Exam.objects.filter(id=exam_id).first()

            attempt, _ = ExamAttempt.objects.get_or_create(
                exam_id=exam_id,
                enrollment_id=enrollment_id,
                attempt_index=1,
                defaults={
                    "submission_id": 0,
                    "is_retake": False,
                    "is_representative": True,
                    "status": "done",
                    "meta": {"source": "manual_entry"},
                },
            )

            result, _ = Result.objects.get_or_create(
                target_type="exam",
                target_id=exam_id,
                enrollment=enrollment_obj,
                defaults={
                    "attempt": attempt,
                    "total_score": 0,
                    "max_score": float(exam_obj.max_score or 0) if exam_obj else 0,
                    "objective_score": 0,
                },
            )
            # Re-fetch with lock
            result = (
                Result.objects
                .select_for_update()
                .filter(id=result.id)
                .first()
            )

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
        # 3️⃣ ResultItem (문항 스냅샷) — 없으면 생성(주관식 수동 입력용)
        # -------------------------------------------------
        item = (
            ResultItem.objects
            .select_for_update()
            .filter(result=result, question_id=question_id)
            .first()
        )
        if not item:
            exam_question = ExamQuestion.objects.filter(id=question_id).first()
            if not exam_question:
                raise NotFound({"detail": "question not found", "code": "NOT_FOUND"})
            max_score = float(getattr(exam_question, "score", 0) or 0.0)
            if new_score < 0 or new_score > max_score:
                raise ValidationError(
                    {
                        "detail": f"score must be between 0 and {max_score}",
                        "code": "INVALID",
                    }
                )
            item = ResultItem.objects.create(
                result=result,
                question_id=question_id,
                answer=new_answer if new_answer is not None else "",
                is_correct=bool(new_score >= max_score),
                score=float(new_score),
                max_score=max_score,
                source="manual",
            )
            item_created = True
        else:
            item_created = False
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
            answer=new_answer if new_answer is not None else (item.answer or ""),
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
        # 5️⃣ ResultItem 업데이트 (기존 건만; 신규 생성 건은 이미 score 반영됨)
        # -------------------------------------------------
        if not item_created:
            item.score = float(new_score)
            item.is_correct = bool(new_score >= max_score)
            item.source = "manual"
            update_fields = ["score", "is_correct", "source"]
            if new_answer is not None:
                item.answer = new_answer
                update_fields.append("answer")
            item.save(update_fields=update_fields)

        # -------------------------------------------------
        # 6️⃣ total_score 재계산 (sum of all ResultItems — objective 이미 포함)
        # -------------------------------------------------
        agg = ResultItem.objects.filter(result=result)
        total_score = sum(float(x.score or 0.0) for x in agg)
        max_total = sum(float(x.max_score or 0.0) for x in agg)

        result.total_score = float(total_score)
        result.max_score = float(max_total)
        result.save(update_fields=["total_score", "max_score"])

        # -------------------------------------------------
        # 7️⃣ progress pipeline 즉시 트리거
        # -------------------------------------------------
        session = get_primary_session_for_exam(exam_id)
        if not session:
            return Response(
                {"detail": "session not found for this exam; cannot recalculate progress", "code": "INVALID"},
                status=drf_status.HTTP_409_CONFLICT,
            )

        # Submission에는 session_id 없음 → exam+enrollment 기준 최신 제출 조회
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
            dispatch_progress_pipeline(submission_id=int(submission.id))
        else:
            dispatch_progress_pipeline(exam_id=int(exam_id))

        # 성적 공개 알림톡 (best-effort)
        try:
            from apps.domains.enrollment.models import Enrollment as _Enr
            from apps.support.messaging.services import send_event_notification
            _exam = Exam.objects.filter(id=exam_id).first()
            enr = _Enr.objects.select_related("student", "lecture").filter(
                id=enrollment_id, tenant=request.tenant
            ).first()
            if enr and enr.student:
                send_event_notification(
                    tenant=request.tenant,
                    trigger="exam_score_published",
                    student=enr.student,
                    send_to="parent",
                    context={
                        "시험명": str(getattr(_exam, "title", "") or ""),
                        "강의명": str(getattr(enr.lecture, "title", "") or ""),
                        "시험성적": f"{int(total_score)}/{int(max_total)}",
                    },
                )
        except Exception:
            logger.debug("exam_score_published notification failed", exc_info=True)

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
