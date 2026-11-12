"""Exam setting for the OMR teacher score strip (Beta).

The flag lives on the structure-owning ``Sheet`` because the PDF renderer and
the AI worker payload both build their OMR contract from that sheet. Reads of
the printed strips are grading-grid proposals only; see docs/domain/omr.md.
"""
from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.domains.assets.omr.services.meta_generator import (
    ESSAY_SCORE_MAX,
    essay_score_mark_availability,
)
from apps.support.omr.contract_builder import (
    build_omr_sheet_contract,
    essay_score_marks_scan_shape,
    supports_essay_score_marks,
)


SCORE_METHOD_REQUIRED = "서술형에 문항별 점수 입력을 사용하는 시험에서만 교사 채점란을 사용할 수 있습니다."


def _state(*, sheet, exam, contract=None) -> dict[str, Any]:
    if sheet is None:
        return {
            "enabled": False,
            "available": False,
            "printed": False,
            "reason": "시험 문항을 먼저 등록해 주세요.",
            "essay_count": 0,
            "max_score": ESSAY_SCORE_MAX,
            "beta": True,
        }
    contract = contract or build_omr_sheet_contract(sheet=sheet, exam=exam)
    essays = [question for question in contract.questions if question.kind == "essay"]
    availability = essay_score_mark_availability(
        question_count=contract.choice_count,
        essay_count=len(essays),
        essay_max_scores=[question.score for question in essays],
    ) if supports_essay_score_marks(exam) else {"available": False, "reason": SCORE_METHOD_REQUIRED}
    return {
        "enabled": bool(sheet.essay_score_marks),
        "available": bool(availability["available"]),
        # True when the next printed sheet carries the strip.
        "printed": bool(contract.essay_score_marks),
        "reason": availability["reason"],
        "essay_count": len(essays),
        "max_score": ESSAY_SCORE_MAX,
        "beta": True,
    }


def essay_score_marks_state(*, exam, tenant) -> dict[str, Any]:
    from apps.support.omr.document_dependencies import resolve_template_exam_for_tenant

    sheet_exam = resolve_template_exam_for_tenant(exam, tenant)
    return _state(sheet=getattr(sheet_exam, "sheet", None), exam=exam)


@transaction.atomic
def set_essay_score_marks(*, exam, tenant, enabled: bool) -> dict[str, Any]:
    from apps.domains.exams.models import Sheet
    from apps.domains.exams.services.structure_copy_service import (
        ensure_regular_exam_owns_structure,
    )
    from apps.domains.exams.services.template_resolver import (
        assert_template_editable,
        resolve_structure_exam,
    )

    if enabled and not supports_essay_score_marks(exam):
        raise ValidationError({"enabled": SCORE_METHOD_REQUIRED})

    # Same ownership rule as question edits: a linked regular exam gets its own
    # structure first, and a sealed template is not changed.
    ensure_regular_exam_owns_structure(exam)
    owner = resolve_structure_exam(exam)
    if int(getattr(owner, "tenant_id", 0) or 0) != int(getattr(tenant, "id", 0) or 0):
        raise ValidationError({"detail": "시험을 찾을 수 없습니다."})
    assert_template_editable(owner)
    sheet = Sheet.objects.select_for_update().filter(exam=owner).first()
    if sheet is None:
        raise ValidationError({"enabled": "시험 문항을 먼저 등록해 주세요."})
    contract = build_omr_sheet_contract(sheet=sheet, exam=exam)
    if enabled:
        state = _state(sheet=sheet, exam=exam, contract=contract)
        if not state["available"]:
            raise ValidationError({"enabled": state["reason"]})
    # Leave recognition available when the teacher stops printing new strips.
    scan_shape = sheet.essay_score_marks_scan_shape
    if enabled or contract.essay_score_marks:
        scan_shape = essay_score_marks_scan_shape(contract)
    if sheet.essay_score_marks != enabled or sheet.essay_score_marks_scan_shape != scan_shape:
        sheet.essay_score_marks = enabled
        sheet.essay_score_marks_scan_shape = scan_shape
        sheet.save(update_fields=["essay_score_marks", "essay_score_marks_scan_shape", "updated_at"])
    return _state(sheet=sheet, exam=exam)


def essay_score_marks_enabled_for_exam(*, exam, tenant) -> bool:
    """Cheap flag read for screens that only need to know the setting."""
    from apps.support.omr.document_dependencies import resolve_template_exam_for_tenant

    if not supports_essay_score_marks(exam):
        return False
    try:
        sheet_exam = resolve_template_exam_for_tenant(exam, tenant)
    except ValueError:
        return False
    sheet = getattr(sheet_exam, "sheet", None)
    return bool(getattr(sheet, "essay_score_marks", False))
