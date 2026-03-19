# apps/domains/assets/omr/services/omr_render_service.py
"""
OMR 렌더링 서비스 v7

SSOT = frontend/public/omr-sheet.html (브라우저 렌더링 + 인쇄/PDF)
서버사이드 PDF 생성은 더 이상 수행하지 않는다.
이 서비스는 OMR 메타 좌표 생성과 URL 구성을 담당한다.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlencode

from apps.domains.assets.omr.services.meta_generator import build_omr_meta


class OMRRenderService:
    """OMR 서비스 — 메타 생성 + URL 구성."""

    @staticmethod
    def get_meta(
        *,
        question_count: int,
        n_choices: int = 5,
        essay_count: int = 0,
    ) -> Dict[str, Any]:
        """OMR 좌표 메타를 반환한다."""
        return build_omr_meta(
            question_count=question_count,
            n_choices=n_choices,
            essay_count=essay_count,
        )

    @staticmethod
    def build_url(
        *,
        exam_name: str = "",
        lecture_name: str = "",
        session_name: str = "",
        mc_count: int = 30,
        essay_count: int = 0,
        n_choices: int = 5,
        base_url: str = "/omr-sheet.html",
    ) -> str:
        """OMR 시트 URL을 구성한다 (프론트엔드 HTML 페이지)."""
        params = {
            "exam": exam_name,
            "lecture": lecture_name,
            "session": session_name,
            "mc": str(mc_count),
            "essay": str(essay_count),
            "choices": str(n_choices),
        }
        return f"{base_url}?{urlencode(params)}"
