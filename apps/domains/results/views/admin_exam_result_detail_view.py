# ==========================================================================================
# FILE: apps/domains/results/views/admin_exam_result_detail_view.py
# ==========================================================================================
"""
Admin Exam Result Detail View (단일 학생 결과 상세)

GET /results/admin/exams/<exam_id>/enrollments/<enrollment_id>/

==========================================================================================
✅ PHASE 3 확정 계약 (FRONTEND LOCK)
==========================================================================================
응답 보장 필드:
- passed                : Exam.pass_score 기준 시험 합불
- clinic_required       : ClinicLink 단일 진실 (자동 트리거만)
- items[].is_editable   : edit_state 기반
- edit_state            : LOCK 판단 메타
- allow_retake
- max_attempts
- can_retake

⚠️ 주의
- passed ≠ SessionProgress.exam_passed
- 이 API는 "시험 단위(Result) 진실"
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import NotFound

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import Result, ExamAttempt
from apps.domains.results.serializers.student_exam_result import (
    StudentExamResultSerializer,
)

from apps.domains.exams.models import Exam, ExamQuestion

# ✅ 단일 진실 유틸
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.results.utils.clinic import is_clinic_required
from apps.domains.results.utils.exam_achievement import compute_exam_achievement
from apps.support.omr.score_shape import get_exam_score_shape

# ✅ OMR 스캔 이미지 presigned URL
from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.support.omr.scan_images import build_omr_scan_image_payload
from apps.domains.results.services.answer_matching import format_answer_for_display


class AdminExamResultDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int, enrollment_id: int):
        exam_id = int(exam_id)
        enrollment_id = int(enrollment_id)

        # ✅ tenant isolation: verify exam belongs to tenant
        exam = get_object_or_404(
            Exam,
            id=exam_id,
            tenant=request.tenant,
            exam_type=Exam.ExamType.REGULAR,
            is_active=True,
            sessions__lecture__tenant=request.tenant,
        )

        # ✅ tenant isolation: verify enrollment belongs to tenant
        from apps.domains.results.guards.enrollment_tenant_guard import validate_enrollment_belongs_to_tenant
        validate_enrollment_belongs_to_tenant(enrollment_id, request.tenant)

        pass_score = float(getattr(exam, "pass_score", 0.0) or 0.0)
        score_shape = get_exam_score_shape(exam)

        # -------------------------------------------------
        # 1️⃣ Result (대표 스냅샷)
        # -------------------------------------------------
        result = (
            Result.objects
            .filter(
                target_type="exam",
                target_id=exam_id,
                enrollment_id=enrollment_id,
            )
            .prefetch_related("items")
            .first()
        )
        if not result:
            # ── Auto-create for manual scoring (답안지 제출 없이 수동 입력) ──
            from apps.domains.enrollment.models import Enrollment
            enrollment_obj = Enrollment.objects.filter(
                id=enrollment_id, tenant=request.tenant
            ).first()
            if not enrollment_obj:
                raise NotFound("enrollment not found for this tenant")

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

            result_obj, _ = Result.objects.get_or_create(
                target_type="exam",
                target_id=exam_id,
                enrollment=enrollment_obj,
                defaults={
                    "attempt": attempt,
                    "total_score": 0,
                    "max_score": float(exam.max_score or 0),
                    "objective_score": 0,
                },
            )
            result = (
                Result.objects
                .filter(id=result_obj.id)
                .prefetch_related("items")
                .first()
            )

        # -------------------------------------------------
        # 2️⃣ passed — compute_exam_achievement(아래)에서 단일 유틸로 계산.
        #    과거 여기서 직접 pass_score 비교하던 로직은 제거(드리프트 원인).
        # -------------------------------------------------

        # -------------------------------------------------
        # 3️⃣ 재시험 정책 (⚠️ 기존 기능 유지)
        # -------------------------------------------------
        allow_retake = bool(getattr(exam, "allow_retake", False))
        max_attempts = int(getattr(exam, "max_attempts", 1) or 1)

        attempt_qs = ExamAttempt.objects.filter(
            exam_id=exam_id,
            enrollment_id=enrollment_id,
        )
        attempt_count = attempt_qs.count()
        can_retake = bool(allow_retake and attempt_count < max_attempts)

        # -------------------------------------------------
        # 4️⃣ clinic_required (단일 진실)
        # -------------------------------------------------
        clinic_required = False
        session = get_primary_session_for_exam(exam_id)
        if session:
            clinic_required = is_clinic_required(
                session=session,
                enrollment_id=enrollment_id,
                include_manual=False,
            )

        # -------------------------------------------------
        # 5️⃣ edit_state (LOCK 규칙)
        # -------------------------------------------------
        edit_state = {
            "can_edit": True,
            "is_locked": False,
            "lock_reason": None,
            "last_updated_by": None,
            "updated_at": None,
        }

        if result.attempt_id:
            attempt = ExamAttempt.objects.filter(id=int(result.attempt_id)).first()
            if attempt and attempt.status == "grading":
                edit_state.update({
                    "can_edit": False,
                    "is_locked": True,
                    "lock_reason": "GRADING",
                })

        # -------------------------------------------------
        # 6️⃣ Serializer + items[].is_editable
        # -------------------------------------------------
        data = StudentExamResultSerializer(result).data

        for item in data.get("items", []):
            qid = int(item.get("question_id") or 0)
            item["question_number"] = score_shape.question_number_by_id.get(qid)
            item["question_kind"] = score_shape.question_kind(qid)
            item["is_editable"] = bool(
                edit_state["can_edit"] and not edit_state["is_locked"]
            )

        # -------------------------------------------------
        # 7️⃣ 최종 응답 (기존 계약 + PHASE 3 확장)
        # -------------------------------------------------
        # -------------------------------------------------
        # 8️⃣ correct_answers (AnswerKey에서 정답 매핑)
        # -------------------------------------------------
        correct_answers = {}
        template_id = exam.effective_template_exam_id
        try:
            from apps.domains.exams.models import AnswerKey
            ak = AnswerKey.objects.get(exam_id=template_id)
            correct_answers = ak.answers or {}
        except AnswerKey.DoesNotExist:
            pass

        for item in data.get("items", []):
            qid = str(item.get("question_id", ""))
            item["correct_answer"] = format_answer_for_display(
                correct_answers.get(qid, "")
            )

        questions_payload = []
        if score_shape.sheet_id:
            for question in (
                ExamQuestion.objects
                .filter(sheet_id=score_shape.sheet_id)
                .only("id", "number", "score")
                .order_by("number")
            ):
                qid = int(question.id)
                kind = score_shape.question_kind(qid)
                payload = {
                    "question_id": qid,
                    "number": int(question.number),
                    "max_score": score_shape.question_max_score(
                        qid,
                        getattr(question, "score", 0),
                    ),
                }
                if kind:
                    payload["kind"] = kind
                questions_payload.append(payload)

        # -------------------------------------------------
        # 9️⃣ OMR 스캔 정보 (image_url + per-answer meta)
        #     — 대표 attempt의 submission을 기반으로 주입
        # -------------------------------------------------
        scan_image_payload = {
            "scan_image_url": "",
            "original_scan_image_url": "",
            "scan_image_is_aligned": False,
            "scan_image_size": None,
        }
        submission_id_for_omr: int | None = None
        submission_status = None
        manual_review_meta = None
        identifier_status = None

        if result.attempt_id:
            att = ExamAttempt.objects.filter(id=int(result.attempt_id)).first()
            if att and att.submission_id:
                submission_id_for_omr = int(att.submission_id)

        if submission_id_for_omr:
            sub = (
                Submission.objects
                .filter(id=submission_id_for_omr, tenant=request.tenant)
                .only("id", "file_key", "status", "meta", "source")
                .first()
            )
            if sub:
                submission_status = sub.status
                s_meta = sub.meta or {}
                manual_review_meta = s_meta.get("manual_review")
                identifier_status = s_meta.get("identifier_status")

                if sub.file_key and sub.source == Submission.Source.OMR_SCAN:
                    scan_image_payload = build_omr_scan_image_payload(
                        submission=sub,
                        expires_in=21600,
                    )

                # Per-answer OMR meta (confidence/marking/status)
                ans_qs = SubmissionAnswer.objects.filter(
                    submission_id=submission_id_for_omr,
                ).only("exam_question_id", "meta")
                omr_by_qid: dict[int, dict] = {}
                for a in ans_qs:
                    am = a.meta or {}
                    omr = am.get("omr") if isinstance(am, dict) else None
                    if isinstance(omr, dict) and omr:
                        omr_by_qid[int(a.exam_question_id)] = omr

                if omr_by_qid:
                    for item in data.get("items", []):
                        qid_int = int(item.get("question_id") or 0)
                        omr = omr_by_qid.get(qid_int)
                        if not omr:
                            continue
                        existing = item.get("meta") or {}
                        merged_omr = dict(existing.get("omr") or {})
                        merged_omr.update(omr)
                        existing["omr"] = merged_omr
                        item["meta"] = existing

        # ✅ 성취 SSOT: student/admin 뷰 공통 유틸로 드리프트 차단.
        achievement_data = compute_exam_achievement(
            enrollment_id=enrollment_id,
            exam_id=exam_id,
            session=session,
            total_score=float(result.total_score or 0.0),
            pass_score=pass_score,
            attempt_id=result.attempt_id,
        )

        data.update({
            "passed": achievement_data["is_pass"],
            "allow_retake": allow_retake,
            "max_attempts": max_attempts,
            "can_retake": can_retake,
            "clinic_required": bool(clinic_required),
            "edit_state": edit_state,
            "correct_answers": {
                str(k): format_answer_for_display(v)
                for k, v in (correct_answers or {}).items()
            },
            "questions": questions_payload,
            "score_shape": {
                "total_questions": int(score_shape.total_questions),
                "choice_count": int(score_shape.choice_count),
                "essay_count": int(score_shape.essay_count),
                "objective_max_score": float(score_shape.objective_max_score),
                "subjective_max_score": float(score_shape.subjective_max_score),
                "total_max_score": float(score_shape.total_max_score),
                "source": str(score_shape.shape_source),
            },
            **scan_image_payload,
            "submission_id": submission_id_for_omr,
            "submission_status": submission_status,
            "manual_review": manual_review_meta,
            "identifier_status": identifier_status,
            # 성취 SSOT 필드
            "remediated": achievement_data["remediated"],
            "final_pass": achievement_data["final_pass"],
            "achievement": achievement_data["achievement"],
            "clinic_retake": achievement_data["clinic_retake"],
            "is_provisional": achievement_data["is_provisional"],
            "meta_status": achievement_data["meta_status"],
        })

        return Response(data)
