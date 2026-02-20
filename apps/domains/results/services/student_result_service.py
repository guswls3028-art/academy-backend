# PATH: apps/domains/results/services/student_result_service.py
"""
단일 진실: 학생용 시험 결과 조회
- GET /results/me/exams/<exam_id>/ (results 앱)
- GET /student/results/me/exams/<exam_id>/ (student_app)
둘 다 이 서비스의 동일 데이터를 반환하도록 사용.
"""
from __future__ import annotations

from django.http import Http404

from apps.domains.results.models import Result, ExamAttempt
from apps.domains.results.serializers.student_exam_result import StudentExamResultSerializer
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.enrollment.models import Enrollment
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.results.utils.clinic import is_clinic_required


def get_my_exam_result_data(request, exam_id: int) -> dict:
    """
    현재 사용자의 시험 결과 스냅샷 + 재시험/클리닉 정책.
    enrollment/result 없으면 Http404.
    """
    user = request.user
    exam_id = int(exam_id)
    exam = Exam.objects.filter(id=exam_id).first()
    if not exam:
        raise Http404("exam not found")

    # 해당 시험의 응시 대상 enrollment만 사용 (한 학생이 여러 강의 수강 시 정확한 결과 조회)
    allowed_enrollment_ids = ExamEnrollment.objects.filter(
        exam_id=exam_id
    ).values_list("enrollment_id", flat=True)
    enrollment_qs = Enrollment.objects.filter(id__in=allowed_enrollment_ids)
    if hasattr(Enrollment, "user_id"):
        enrollment_qs = enrollment_qs.filter(user_id=user.id)
    elif hasattr(Enrollment, "student_id"):
        enrollment_qs = enrollment_qs.filter(student_id=user.id)
    else:
        enrollment_qs = enrollment_qs.filter(student__user=user)

    enrollment = enrollment_qs.first()
    if not enrollment:
        raise Http404("enrollment not found")

    enrollment_id = int(enrollment.id)

    result = (
        Result.objects
        .filter(target_type="exam", target_id=exam_id, enrollment_id=enrollment_id)
        .prefetch_related("items")
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

    data["exam_id"] = exam_id
    pass_score = float(getattr(exam, "pass_score", 0) or 0)
    data["is_pass"] = float(result.total_score) >= pass_score

    for item in data.get("items") or []:
        item.setdefault("question_number", item.get("question_id"))
        item.setdefault("student_answer", item.get("answer"))
        item.setdefault("correct_answer", None)

    return data
