# PATH: apps/domains/results/services/student_result_service.py
"""
단일 진실: 학생용 시험 결과 조회
- GET /results/me/exams/<exam_id>/ (results 앱)
- GET /student/results/me/exams/<exam_id>/ (student_app)
둘 다 이 서비스의 동일 데이터를 반환하도록 사용.
"""
from __future__ import annotations

from django.http import Http404

from academy.adapters.db.django import repositories_exams as exams_repo
from apps.domains.results.models import Result, ExamAttempt
from apps.domains.results.serializers.student_exam_result import StudentExamResultSerializer
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.results.utils.clinic import is_clinic_required
from apps.domains.results.utils.ranking import compute_exam_rankings
from apps.domains.results.utils.exam_achievement import compute_exam_achievement
from apps.domains.results.services.answer_matching import format_answer_for_display
from apps.domains.results.aggregations.exam_report import summarize_result_items
from apps.support.results.student_result_dependencies import (
    active_enrollments_for_student,
    get_request_student,
)


def active_exam_enrollment_ids_for_student(*, tenant, student, exam_id: int) -> list[int]:
    """Return active enrollment ids that connect one student to one tenant exam."""
    allowed_enrollment_ids = exams_repo.exam_enrollment_ids_for_tenant_exam(exam_id, tenant)
    return list(
        active_enrollments_for_student(
            tenant=tenant,
            student=student,
        )
        .filter(id__in=allowed_enrollment_ids)
        .values_list("id", flat=True)
    )


def get_my_exam_result_data(request, exam_id: int, tenant=None) -> dict:
    """
    현재 사용자의 시험 결과 스냅샷 + 재시험/클리닉 정책.
    enrollment/result 없으면 Http404.
    tenant: 테넌트 격리를 위해 반드시 전달해야 함.
    """
    if tenant is None:
        tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404("tenant resolution failed")
    exam_id = int(exam_id)
    exam = exams_repo.regular_active_exam_for_tenant(exam_id, tenant)
    if not exam:
        raise Http404("exam not found")
    student = get_request_student(request)
    if not student:
        raise Http404("student not found")

    enrollment_ids = active_exam_enrollment_ids_for_student(
        tenant=tenant,
        student=student,
        exam_id=exam_id,
    )
    enrollment = (
        active_enrollments_for_student(tenant=tenant, student=student)
        .filter(id__in=enrollment_ids)
        .order_by("id")
        .first()
    )
    if not enrollment:
        raise Http404("enrollment not found")

    enrollment_id = int(enrollment.id)

    result = (
        Result.objects
        .filter(target_type="exam", target_id=exam_id, enrollment_id=enrollment_id)
        .prefetch_related("items")
        .order_by("-id")
        .first()
    )
    if not result:
        raise Http404("result not found")

    allow_retake = bool(getattr(exam, "allow_retake", False))
    max_attempts = int(getattr(exam, "max_attempts", 1) or 1)
    attempt_count = ExamAttempt.objects.filter(
        exam_id=exam_id,
        enrollment_id=enrollment_id,
    ).count()
    can_retake = bool(allow_retake and attempt_count < max_attempts)

    clinic_required = False
    session = get_primary_session_for_exam(exam_id)
    if session:
        clinic_required = is_clinic_required(
            session=session,
            enrollment_id=enrollment_id,
            include_manual=False,
        )

    data = StudentExamResultSerializer(result).data
    data["allow_retake"] = allow_retake
    data["max_attempts"] = max_attempts
    data["can_retake"] = can_retake
    data["clinic_required"] = bool(clinic_required)

    # ✅ 성취 계산 (SSOT: utils/exam_achievement)
    # student/admin 뷰가 동일 유틸을 사용해 드리프트 재발을 구조적으로 방지.
    pass_score = float(getattr(exam, "pass_score", 0) or 0)
    achievement_data = compute_exam_achievement(
        enrollment_id=enrollment_id,
        exam_id=exam_id,
        session=session,
        total_score=float(result.total_score or 0.0),
        pass_score=pass_score,
        attempt_id=result.attempt_id,
        tenant=tenant,
    )
    data["exam_id"] = exam_id
    data["meta_status"] = achievement_data["meta_status"]
    data["is_pass"] = achievement_data["is_pass"]
    data["remediated"] = achievement_data["remediated"]
    data["clinic_retake"] = achievement_data["clinic_retake"]
    data["final_pass"] = achievement_data["final_pass"]
    data["is_provisional"] = achievement_data["is_provisional"]
    data["achievement"] = achievement_data["achievement"]

    is_not_submitted = achievement_data["meta_status"] == "NOT_SUBMITTED"
    is_provisional = achievement_data["is_provisional"]

    # 정답 공개 정책 적용
    # provisional/미응시/불합격 → 비공개, 합격/기준없음 → 정책 따름
    is_pass = data["is_pass"]
    if is_provisional or is_not_submitted:
        show_answers = False
    elif is_pass is None or is_pass:
        show_answers = exam.should_show_answers()
    else:
        show_answers = False
    data["answer_visibility"] = getattr(exam, "answer_visibility", "hidden")
    data["answers_visible"] = show_answers

    # question_id → question_number 매핑 (ExamQuestion.number 사용)
    item_question_ids = [
        item.get("question_id") for item in (data.get("items") or [])
        if item.get("question_id")
    ]
    template_exam_id = exam.effective_template_exam_id
    question_number_map = exams_repo.exam_question_number_map(
        item_question_ids,
        exam_id=template_exam_id,
        tenant=tenant,
    )
    data["items"] = [
        item for item in (data.get("items") or [])
        if item.get("question_id") in question_number_map
    ]

    # 정답 공개 시 answer key에서 correct_answer 주입
    correct_answer_map = {}
    if show_answers:
        correct_answer_map = exams_repo.answer_key_answers_for_exam(
            template_exam_id,
            tenant=tenant,
        )

    for item in data.get("items") or []:
        q_id = item.get("question_id")
        item["question_number"] = question_number_map.get(q_id, q_id)
        item.setdefault("student_answer", item.get("answer"))
        if show_answers:
            correct = correct_answer_map.get(str(q_id or ""))
            item["correct_answer"] = format_answer_for_display(correct) if correct else None
        else:
            item["correct_answer"] = None

    data["analysis"] = summarize_result_items(data.get("items") or [])

    # 석차 정보 추가
    rank_map = compute_exam_rankings(exam_id=exam_id, tenant=tenant)
    rank_info = rank_map.get(enrollment_id, {})
    data["rank"] = rank_info.get("rank")
    data["percentile"] = rank_info.get("percentile")
    data["cohort_size"] = rank_info.get("cohort_size")
    data["cohort_avg"] = rank_info.get("cohort_avg")

    return data
