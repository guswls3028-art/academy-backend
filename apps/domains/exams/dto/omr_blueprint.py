# apps/domains/exams/dto/omr_blueprint.py
"""
OMR Blueprint v7 DTO

meta_generator.py 의 build_omr_meta() 결과를 타입 안전하게 다루는 DTO.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Any, Optional


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class ROI:
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class Bubble:
    label: str          # "1"~"5" (객관식) 또는 "0"~"9" (식별번호)
    center: Point
    radius_x: float     # 쌀톨형: 가로 반지름
    radius_y: float     # 쌀톨형: 세로 반지름


@dataclass(frozen=True)
class QuestionBlueprint:
    question_number: int
    question_type: str  # "choice"
    roi: ROI
    choices: List[Bubble]


@dataclass(frozen=True)
class IdentifierDigitBlueprint:
    digit_index: int    # 0~7
    bubbles: List[Bubble]


@dataclass(frozen=True)
class OMRBlueprint:
    version: str        # "v7"
    units: str          # "mm"
    mc_count: int
    essay_count: int
    n_choices: int
    page: Dict[str, Any]
    identifier: Optional[Dict[str, Any]]
    questions: List[QuestionBlueprint]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "units": self.units,
            "mc_count": self.mc_count,
            "essay_count": self.essay_count,
            "n_choices": self.n_choices,
            "page": self.page,
            "identifier": self.identifier,
            "questions": [
                {
                    "question_number": q.question_number,
                    "type": q.question_type,
                    "roi": {"x": q.roi.x, "y": q.roi.y, "w": q.roi.w, "h": q.roi.h},
                    "choices": [
                        {
                            "label": b.label,
                            "center": {"x": b.center.x, "y": b.center.y},
                            "radius_x": b.radius_x,
                            "radius_y": b.radius_y,
                        }
                        for b in q.choices
                    ],
                }
                for q in self.questions
            ],
        }

    @classmethod
    def from_meta(cls, meta: Dict[str, Any]) -> "OMRBlueprint":
        """build_omr_meta() 결과를 OMRBlueprint로 변환."""
        questions = []
        for q in meta.get("questions", []):
            roi_d = q.get("roi", {})
            choices = [
                Bubble(
                    label=c["label"],
                    center=Point(x=c["center"]["x"], y=c["center"]["y"]),
                    radius_x=c["radius_x"],
                    radius_y=c["radius_y"],
                )
                for c in q.get("choices", [])
            ]
            questions.append(QuestionBlueprint(
                question_number=q["question_number"],
                question_type=q.get("type", "choice"),
                roi=ROI(x=roi_d["x"], y=roi_d["y"], w=roi_d["w"], h=roi_d["h"]),
                choices=choices,
            ))

        return cls(
            version=meta.get("version", "v7"),
            units=meta.get("units", "mm"),
            mc_count=meta.get("mc_count", 0),
            essay_count=meta.get("essay_count", 0),
            n_choices=meta.get("n_choices", 5),
            page=meta.get("page", {}),
            identifier=meta.get("identifier"),
            questions=questions,
        )
