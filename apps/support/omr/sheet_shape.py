from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OMRSheetShape:
    total_questions: int
    choice_count: int
    essay_count: int
    source: str


def resolve_omr_sheet_shape(*, sheet, exam=None) -> OMRSheetShape:
    """
    Resolve the rendered OMR shape for a sheet.

    Compatibility facade for legacy call sites. New OMR paths should consume
    the full OMRSheetContract from apps.support.omr.contract_builder.
    """
    from apps.support.omr.contract_builder import build_omr_sheet_contract

    contract = build_omr_sheet_contract(sheet=sheet, exam=exam)
    return OMRSheetShape(
        total_questions=contract.total_questions,
        choice_count=contract.choice_count,
        essay_count=contract.essay_count,
        source=contract.shape_source,
    )
