# apps/domains/assets/omr/services/omr_asset_service.py
"""
⚠️ DEPRECATED — 레거시 OMR 메타 서비스.
새 시스템은 omr_document_service.py를 사용합니다.
"""
from __future__ import annotations

from typing import Any, Dict

from apps.domains.assets.omr.services.meta_generator import build_omr_meta


class OMRAssetService:
    """OMR 메타 생성 서비스."""

    @staticmethod
    def get_meta(
        *,
        question_count: int = 30,
        n_choices: int = 5,
        essay_count: int = 0,
    ) -> Dict[str, Any]:
        """시험 문항 구성에 맞는 OMR 메타를 반환한다."""
        return build_omr_meta(
            question_count=question_count,
            n_choices=n_choices,
            essay_count=essay_count,
        )
