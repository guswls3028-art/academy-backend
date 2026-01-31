# apps/domains/exams/dto/omr_blueprint.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, List, Dict, Any, Optional


Axis = Literal["x", "y"]
Choice = Literal["A", "B", "C", "D", "E"]


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
    choice: Choice
    center: Point
    radius: float


@dataclass(frozen=True)
class QuestionBlueprint:
    question_number: int
    axis: Axis
    roi: ROI
    choices: List[Bubble]


@dataclass(frozen=True)
class IdentifierDigitBlueprint:
    digit: int
    bubbles: List[Dict[str, Any]]  # 계약은 assets/meta 쪽이 이미 있으니 느슨하게


@dataclass(frozen=True)
class OMRBlueprint:
    version: Literal["objective_v1"]
    units: Literal["mm"]
    question_count: int
    page: Dict[str, Any]
    identifier: Optional[Dict[str, Any]]
    questions: List[QuestionBlueprint]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "units": self.units,
            "question_count": self.question_count,
            "page": self.page,
            "identifier": self.identifier,
            "questions": [
                {
                    "question_number": q.question_number,
                    "axis": q.axis,
                    "roi": {"x": q.roi.x, "y": q.roi.y, "w": q.roi.w, "h": q.roi.h},
                    "choices": [
                        {
                            "choice": b.choice,
                            "center": {"x": b.center.x, "y": b.center.y},
                            "radius": b.radius,
                        }
                        for b in q.choices
                    ],
                }
                for q in self.questions
            ],
        }
