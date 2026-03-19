# apps/domains/assets/omr/services/omr_document_service.py
"""
OMR Document 서비스 — OMRDocument DTO 생성 SSOT

시험/테넌트 데이터에서 OMR 문서를 조립한다.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

from apps.domains.assets.omr.dto.omr_document import OMRDocument

logger = logging.getLogger(__name__)

# 기본 로고 경로 (테넌트 로고가 없을 때 사용)
_DEFAULT_LOGO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "renderer", "fonts", "omr-default-logo.png",
)


class OMRDocumentService:
    """OMR 문서 생성 서비스."""

    @staticmethod
    def from_exam(
        *,
        exam,
        tenant,
        mc_count: Optional[int] = None,
        essay_count: Optional[int] = None,
        n_choices: int = 5,
        exam_title: Optional[str] = None,
        lecture_name: Optional[str] = None,
        session_name: Optional[str] = None,
    ) -> OMRDocument:
        """
        시험에서 기본값 resolve + 사용자 override 적용.

        exam: Exam 인스턴스
        tenant: Tenant 인스턴스
        나머지: 사용자 override (None이면 exam에서 추출)
        """
        # 시험 제목
        title = exam_title if exam_title else exam.title

        # session/lecture 이름 resolve
        _lecture_name = ""
        _session_name = ""
        first_session = exam.sessions.select_related("lecture").first()
        if first_session:
            _session_name = first_session.title or ""
            _lecture_name = first_session.lecture.title if first_session.lecture else ""

        if lecture_name is not None:
            _lecture_name = lecture_name
        if session_name is not None:
            _session_name = session_name

        # 문항 수 resolve
        _mc_count = mc_count
        if _mc_count is None:
            sheet = getattr(exam, "sheet", None)
            total_q = int(getattr(sheet, "total_questions", 0) or 0)
            _mc_count = total_q if total_q > 0 else 20

        _essay_count = essay_count if essay_count is not None else 0

        # 로고 resolve
        logo_url = OMRDocumentService._resolve_logo_url(tenant)
        # HTML 프리뷰에서 기본 로고 표시용 (logo_url이 없으면 기본 SVG)
        if not logo_url:
            logo_url = "/omr-default-logo.svg"

        return OMRDocument(
            exam_title=title,
            lecture_name=_lecture_name,
            session_name=_session_name,
            mc_count=_mc_count,
            essay_count=_essay_count,
            n_choices=n_choices,
            logo_url=logo_url,
        )

    @staticmethod
    def from_params(
        *,
        tenant,
        exam_title: str,
        lecture_name: str = "",
        session_name: str = "",
        mc_count: int = 30,
        essay_count: int = 0,
        n_choices: int = 5,
    ) -> OMRDocument:
        """도구 페이지용. 시험 없이 직접 파라미터로 OMRDocument 생성."""
        logo_url = OMRDocumentService._resolve_logo_url(tenant)
        if not logo_url:
            logo_url = "/omr-default-logo.svg"

        return OMRDocument(
            exam_title=exam_title,
            lecture_name=lecture_name,
            session_name=session_name,
            mc_count=mc_count,
            essay_count=essay_count,
            n_choices=n_choices,
            logo_url=logo_url,
        )

    @staticmethod
    def _resolve_logo_url(tenant) -> Optional[str]:
        """테넌트 로고 presigned URL resolve."""
        try:
            from apps.core.models import Program
            from apps.infrastructure.storage.r2 import resolve_admin_logo_url

            program = Program.objects.filter(tenant=tenant).first()
            if not program:
                return None

            ui = program.ui_config or {}
            logo_key = ui.get("logo_key")
            logo_url = ui.get("logo_url")

            if not logo_key and not logo_url:
                return None

            return resolve_admin_logo_url(logo_key=logo_key, logo_url=logo_url)
        except Exception:
            logger.warning("OMR 로고 resolve 실패", exc_info=True)
            return None

    @staticmethod
    def fetch_logo_bytes(doc: OMRDocument) -> OMRDocument:
        """
        PDF 렌더링용: logo_url에서 이미지 바이너리를 다운로드하여 OMRDocument에 추가.
        로고가 없거나 다운로드 실패 시 기본 로고 사용.
        """
        # 1) 절대 URL인 테넌트 로고가 있으면 다운로드 시도
        if doc.logo_url and doc.logo_url.startswith("http"):
            try:
                resp = requests.get(doc.logo_url, timeout=5)
                resp.raise_for_status()
                mime = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
                return doc.with_logo_bytes(resp.content, mime)
            except Exception:
                logger.warning("OMR 로고 다운로드 실패: %s", doc.logo_url, exc_info=True)

        # 2) 테넌트 로고 없음 또는 상대 경로(기본값) 또는 실패 → 기본 로고 사용
        return OMRDocumentService._apply_default_logo(doc)

    @staticmethod
    def _apply_default_logo(doc: OMRDocument) -> OMRDocument:
        """기본 로고(PNG) 적용."""
        try:
            if os.path.isfile(_DEFAULT_LOGO_PATH):
                with open(_DEFAULT_LOGO_PATH, "rb") as f:
                    return doc.with_logo_bytes(f.read(), "image/png")
        except Exception:
            logger.warning("기본 로고 로드 실패", exc_info=True)
        return doc
