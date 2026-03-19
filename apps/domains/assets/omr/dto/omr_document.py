# apps/domains/assets/omr/dto/omr_document.py
"""
OMR Document DTO — 렌더링용 문서 모델 (SSOT)

preview(HTML)와 PDF가 동일한 이 모델을 입력으로 사용한다.
좌표/레이아웃 상수는 meta_generator.py에 유지 — 여기에 복제하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class OMRDocument:
    """OMR 답안지 렌더링에 필요한 모든 콘텐츠 데이터."""

    # -- 시험 정보 --
    exam_title: str  # "제1회 단원평가"
    lecture_name: str = ""  # "수학"
    session_name: str = ""  # "1차시"

    # -- 문항 구성 --
    mc_count: int = 20  # 0~45
    essay_count: int = 0  # 0~10
    n_choices: int = 5  # 4 or 5

    # -- 테넌트 브랜딩 --
    logo_url: Optional[str] = None  # presigned URL (HTML preview용)
    logo_bytes: Optional[bytes] = None  # 로고 바이너리 (PDF 렌더용)
    logo_mime: Optional[str] = None  # "image/png" 등

    def with_logo_bytes(
        self, logo_bytes: Optional[bytes], logo_mime: Optional[str] = None
    ) -> OMRDocument:
        """로고 바이너리가 추가된 새 인스턴스를 반환."""
        return replace(self, logo_bytes=logo_bytes, logo_mime=logo_mime)

    def validate(self) -> list[str]:
        """유효성 검사. 오류 메시지 리스트 반환 (빈 리스트면 유효)."""
        errors = []
        if not self.exam_title or not self.exam_title.strip():
            errors.append("시험명은 필수입니다.")
        if self.mc_count < 0 or self.mc_count > 45:
            errors.append("객관식 문항 수는 0~45 사이여야 합니다.")
        if self.essay_count < 0 or self.essay_count > 10:
            errors.append("서술형 문항 수는 0~10 사이여야 합니다.")
        if self.mc_count + self.essay_count < 1:
            errors.append("문항이 최소 1개 이상이어야 합니다.")
        if self.n_choices != 5:
            errors.append("보기 수는 5여야 합니다.")
        return errors

    def to_defaults_dict(self) -> dict:
        """프론트엔드 defaults 응답용."""
        return {
            "exam_title": self.exam_title,
            "lecture_name": self.lecture_name,
            "session_name": self.session_name,
            "mc_count": self.mc_count,
            "essay_count": self.essay_count,
            "n_choices": self.n_choices,
            "logo_url": self.logo_url,
        }
