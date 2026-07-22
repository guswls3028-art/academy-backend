# apps/domains/assets/omr/dto/omr_document.py
"""
OMR Document DTO — 렌더링용 문서 모델 (SSOT)

preview(HTML)와 PDF가 동일한 이 모델을 입력으로 사용한다.
좌표/레이아웃 상수는 meta_generator.py에 유지 — 여기에 복제하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from apps.domains.assets.omr.services.meta_generator import MAX_MC_QUESTIONS, validate_layout


DECORATIVE_ESSAY_COUNT = 5
MAX_ESSAY_QUESTIONS = 20
MAX_MC_WITH_OPTIONAL_ESSAY_AREA = 40


@dataclass(frozen=True)
class OMRDocument:
    """OMR 답안지 렌더링에 필요한 모든 콘텐츠 데이터."""

    # -- 시험 정보 --
    exam_title: str  # "제1회 단원평가"
    lecture_name: str = ""  # "수학"
    session_name: str = ""  # "1차시"

    # -- 문항 구성 --
    mc_count: int = 20  # 0~MAX_MC_QUESTIONS
    essay_count: int = 0  # 0~MAX_ESSAY_QUESTIONS
    n_choices: int = 5  # 4 or 5
    include_optional_essay_area: bool = True
    decorative_essay_count: int = DECORATIVE_ESSAY_COUNT
    choice_question_numbers: tuple[int, ...] = ()
    essay_question_numbers: tuple[int, ...] = ()

    # -- 테넌트 브랜딩 --
    logo_url: Optional[str] = None  # presigned URL (HTML preview용)
    logo_bytes: Optional[bytes] = None  # 로고 바이너리 (PDF 렌더용)
    logo_mime: Optional[str] = None  # "image/png" 등
    brand_color: Optional[str] = None  # 테넌트 프라이머리 컬러 (e.g. "#3B82F6")

    def with_logo_bytes(
        self, logo_bytes: Optional[bytes], logo_mime: Optional[str] = None
    ) -> OMRDocument:
        """로고 바이너리가 추가된 새 인스턴스를 반환."""
        return replace(self, logo_bytes=logo_bytes, logo_mime=logo_mime)

    @property
    def render_essay_count(self) -> int:
        if self.essay_count > 0:
            return self.essay_count
        if self.can_include_optional_essay_area and self.include_optional_essay_area:
            return self.decorative_essay_count
        return 0

    @property
    def resolved_choice_question_numbers(self) -> tuple[int, ...]:
        return self.choice_question_numbers or tuple(range(1, self.mc_count + 1))

    @property
    def resolved_essay_question_numbers(self) -> tuple[int, ...]:
        if self.essay_count <= 0:
            return ()
        return self.essay_question_numbers or tuple(
            range(self.mc_count + 1, self.mc_count + self.essay_count + 1)
        )

    @property
    def can_include_optional_essay_area(self) -> bool:
        return (
            self.essay_count <= 0
            and 0 < self.mc_count <= MAX_MC_WITH_OPTIONAL_ESSAY_AREA
        )

    @property
    def has_decorative_essay_area(self) -> bool:
        return (
            self.essay_count <= 0
            and self.mc_count > 0
            and self.render_essay_count > 0
        )

    @property
    def render_essay_label(self) -> str:
        if self.has_decorative_essay_area:
            return "단답형 공간"
        return f"단답형 {self.render_essay_count}문항"

    def validate(self) -> list[str]:
        """유효성 검사. 오류 메시지 리스트 반환 (빈 리스트면 유효)."""
        errors = []
        if not self.exam_title or not self.exam_title.strip():
            errors.append("시험명은 필수입니다.")
        if self.mc_count < 0 or self.mc_count > MAX_MC_QUESTIONS:
            errors.append(f"객관식 문항 수는 0~{MAX_MC_QUESTIONS} 사이여야 합니다.")
        if self.essay_count < 0 or self.essay_count > MAX_ESSAY_QUESTIONS:
            errors.append(f"단답형 문항 수는 0~{MAX_ESSAY_QUESTIONS} 사이여야 합니다.")
        if self.decorative_essay_count < 0 or self.decorative_essay_count > MAX_ESSAY_QUESTIONS:
            errors.append(
                f"표시용 단답형 문항 수는 0~{MAX_ESSAY_QUESTIONS} 사이여야 합니다."
            )
        if self.mc_count + self.essay_count < 1:
            errors.append("문항이 최소 1개 이상이어야 합니다.")
        if self.n_choices != 5:
            errors.append("보기 수는 5여야 합니다.")
        if self.choice_question_numbers and len(self.choice_question_numbers) != self.mc_count:
            errors.append("객관식 문항 번호 수가 객관식 문항 수와 일치해야 합니다.")
        if self.essay_question_numbers and len(self.essay_question_numbers) != self.essay_count:
            errors.append("단답형 문항 번호 수가 단답형 문항 수와 일치해야 합니다.")
        explicit_numbers = self.resolved_choice_question_numbers + self.resolved_essay_question_numbers
        if len(explicit_numbers) != len(set(explicit_numbers)) or any(n <= 0 for n in explicit_numbers):
            errors.append("문항 번호는 중복 없는 양의 정수여야 합니다.")
        elif set(explicit_numbers) != set(range(1, self.mc_count + self.essay_count + 1)):
            errors.append("문항 번호는 1번부터 전체 문항 수까지 빠짐없이 있어야 합니다.")
        if (
            0 <= self.mc_count <= MAX_MC_QUESTIONS
            and 0 <= self.render_essay_count <= MAX_ESSAY_QUESTIONS
        ):
            errors.extend(validate_layout(self.mc_count, self.render_essay_count))
        return errors

    def to_defaults_dict(self) -> dict:
        """프론트엔드 defaults 응답용."""
        return {
            "exam_title": self.exam_title,
            "lecture_name": self.lecture_name,
            "session_name": self.session_name,
            "mc_count": self.mc_count,
            "essay_count": self.essay_count,
            "include_optional_essay_area": self.include_optional_essay_area,
            "can_include_optional_essay_area": self.can_include_optional_essay_area,
            "render_essay_count": self.render_essay_count,
            "render_essay_label": self.render_essay_label,
            "n_choices": self.n_choices,
            "question_types": [
                "choice" if number in set(self.resolved_choice_question_numbers) else "essay"
                for number in range(1, self.mc_count + self.essay_count + 1)
            ],
            "choice_question_numbers": list(self.resolved_choice_question_numbers),
            "essay_question_numbers": list(self.resolved_essay_question_numbers),
            "logo_url": self.logo_url,
        }
