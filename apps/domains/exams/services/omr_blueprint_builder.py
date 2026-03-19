# apps/domains/exams/services/omr_blueprint_builder.py
"""
OMR Blueprint Builder v7

meta_generator.py(좌표 SSOT)에서 메타를 생성하고
OMRBlueprint DTO로 변환한다.
"""
from __future__ import annotations

from typing import Any, Dict

from apps.domains.assets.omr.services.meta_generator import build_omr_meta
from apps.domains.exams.dto.omr_blueprint import OMRBlueprint


class OMRBlueprintBuilder:
    """
    시험의 문항 구성에 맞는 OMR Blueprint를 생성한다.
    좌표는 meta_generator.py가 SSOT.
    """

    @staticmethod
    def build(
        *,
        question_count: int,
        n_choices: int = 5,
        essay_count: int = 0,
    ) -> Dict[str, Any]:
        """메타 dict 반환."""
        return build_omr_meta(
            question_count=question_count,
            n_choices=n_choices,
            essay_count=essay_count,
        )

    @staticmethod
    def build_strict(
        *,
        question_count: int,
        n_choices: int = 5,
        essay_count: int = 0,
    ) -> OMRBlueprint:
        """엄격한 DTO 반환."""
        meta = build_omr_meta(
            question_count=question_count,
            n_choices=n_choices,
            essay_count=essay_count,
        )
        return OMRBlueprint.from_meta(meta)
