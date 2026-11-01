"""Tenant-scoped grading configuration checks for durable OMR readiness."""

from django.core.exceptions import ValidationError


def omr_grading_configuration_missing(*, tenant_id: int, exam_id: int) -> list[str]:
    from apps.domains.exams.models import Exam
    from apps.support.results.grading_contract_dependencies import validate_exam_grading_contract

    exam = Exam.objects.filter(id=int(exam_id), tenant_id=int(tenant_id)).first()
    if exam is None:
        return ["exam"]
    try:
        validate_exam_grading_contract(exam, require_omr_answers=True)
    except ValidationError as exc:
        code = getattr(exc, "code", None)
        return [code if code in {"sheet", "answer_key"} else "grading_configuration"]
    return []


def has_submission_grading_result(*, tenant_id: int, exam_id: int, submission_id: int) -> bool:
    from apps.domains.results.models import ExamResult, Result

    return ExamResult.objects.filter(
        submission_id=int(submission_id), submission__tenant_id=int(tenant_id),
        exam_id=int(exam_id), exam__tenant_id=int(tenant_id),
    ).exists() or Result.objects.filter(
        target_type="exam", target_id=int(exam_id), enrollment__tenant_id=int(tenant_id),
        attempt__submission_id=int(submission_id), attempt__exam_id=int(exam_id),
    ).exists()
