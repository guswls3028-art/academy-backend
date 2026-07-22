from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


OMR_CONTRACT_SCHEMA_VERSION = "omr_sheet_contract.v2"
OMR_CONTRACT_LAYOUT_VERSION = "omr_pdf_layout.v15"


@dataclass(frozen=True)
class OMRQuestionContract:
    number: int
    kind: str
    exam_question_id: int | None = None
    score: float | None = None

    def __post_init__(self) -> None:
        if self.number <= 0:
            raise ValueError("question number must be positive")
        if self.kind not in {"choice", "essay"}:
            raise ValueError("question kind must be choice or essay")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "number": self.number,
            "kind": self.kind,
        }
        if self.exam_question_id is not None:
            data["exam_question_id"] = self.exam_question_id
        if self.score is not None:
            data["score"] = self.score
        return data


@dataclass(frozen=True)
class OMRSheetContract:
    sheet_id: int | None
    exam_id: int | None
    template_exam_id: int | None
    total_questions: int
    choice_count: int
    essay_count: int
    n_choices: int = 5
    shape_source: str = "unknown"
    schema_version: str = OMR_CONTRACT_SCHEMA_VERSION
    layout_version: str = OMR_CONTRACT_LAYOUT_VERSION
    questions: tuple[OMRQuestionContract, ...] = field(default_factory=tuple)
    template_meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.total_questions < 0 or self.choice_count < 0 or self.essay_count < 0:
            raise ValueError("question counts must be non-negative")
        if self.choice_count + self.essay_count != self.total_questions:
            raise ValueError("choice_count + essay_count must equal total_questions")
        if self.n_choices <= 0:
            raise ValueError("n_choices must be positive")

    @property
    def objective_question_numbers(self) -> tuple[int, ...]:
        return tuple(
            question.number
            for question in self.questions
            if question.kind == "choice"
        )

    @property
    def objective_question_ids_by_number(self) -> dict[int, int]:
        return {
            question.number: question.exam_question_id
            for question in self.questions
            if (
                question.kind == "choice" and question.exam_question_id is not None
            )
        }

    @property
    def essay_question_numbers(self) -> tuple[int, ...]:
        return tuple(question.number for question in self.questions if question.kind == "essay")

    def worker_shape(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "layout_version": self.layout_version,
            "question_count": self.choice_count,
            "mc_count": self.choice_count,
            "essay_count": self.essay_count,
            "total_question_count": self.total_questions,
            "n_choices": self.n_choices,
            "shape_source": self.shape_source,
            "contract_fingerprint": self.fingerprint(),
        }

    def to_dict(self, *, include_template_meta: bool = False) -> dict[str, Any]:
        data = self._canonical_dict(include_template_meta=include_template_meta)
        data["fingerprint"] = self.fingerprint()
        return data

    def fingerprint(self) -> str:
        canonical = self._canonical_dict(include_template_meta=True)
        payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _canonical_dict(self, *, include_template_meta: bool) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "layout_version": self.layout_version,
            "sheet_id": self.sheet_id,
            "exam_id": self.exam_id,
            "template_exam_id": self.template_exam_id,
            "total_questions": self.total_questions,
            "choice_count": self.choice_count,
            "essay_count": self.essay_count,
            "n_choices": self.n_choices,
            "shape_source": self.shape_source,
            "objective_question_numbers": list(self.objective_question_numbers),
            "objective_question_ids_by_number": {
                str(number): exam_question_id
                for number, exam_question_id in self.objective_question_ids_by_number.items()
            },
            "essay_question_numbers": list(self.essay_question_numbers),
            "questions": [question.to_dict() for question in self.questions],
        }
        layout = self.template_meta.get("layout") if isinstance(self.template_meta, dict) else None
        if layout:
            data["layout"] = layout
        if include_template_meta:
            data["template_meta"] = self.template_meta
        return data
