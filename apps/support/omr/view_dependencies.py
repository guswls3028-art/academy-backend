"""Cross-domain dependencies for OMR asset views."""

from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404


def get_exam_for_omr_document(*, tenant, exam_id: int):
    from apps.domains.exams.models import Exam

    return get_object_or_404(
        Exam.objects.filter(
            Q(sessions__lecture__tenant=tenant)
            | Q(derived_exams__sessions__lecture__tenant=tenant)
        ).distinct(),
        id=int(exam_id),
    )


def omr_template_assets_for_tenant(*, tenant, exam_id: int | None = None):
    from apps.domains.exams.models import ExamAsset

    qs = ExamAsset.objects.filter(
        asset_type="OMR_TEMPLATE",
        exam__sessions__lecture__tenant=tenant,
    ).distinct()
    if exam_id:
        qs = qs.filter(exam_id=exam_id)
    return qs


def get_omr_sheet_asset_for_tenant(*, tenant, asset_id: int):
    from apps.domains.exams.models import ExamAsset

    return (
        ExamAsset.objects
        .filter(
            id=asset_id,
            asset_type=ExamAsset.AssetType.OMR_SHEET,
        )
        .filter(
            Q(exam__tenant=tenant)
            | Q(exam__sessions__lecture__tenant=tenant)
            | Q(exam__derived_exams__sessions__lecture__tenant=tenant)
        )
        .first()
    )
