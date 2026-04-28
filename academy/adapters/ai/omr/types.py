# apps/worker/ai/omr/types.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Literal


OMRStatus = Literal["ok", "blank", "ambiguous", "low_confidence", "error"]
OMRMarking = Literal["blank", "single", "multi"]


@dataclass(frozen=True)
class OMRAnswerV1:
    """
    Worker-side OMR payload v1 (question-level)
    This should be embedded into API-side SubmissionAnswer.meta["omr"] later.

    - version: "v1" fixed
    - detected: list[str] ex) ["B"] or ["B","D"]
    - marking: blank/single/multi
    - confidence: 0~1 (top mark confidence)
    - status: ok/blank/ambiguous/low_confidence/error
    - raw: debug info (optional)
    """
    version: str
    question_id: int

    detected: List[str]
    marking: OMRMarking
    confidence: float
    status: OMRStatus

    raw: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "question_id": int(self.question_id),
            "detected": list(self.detected or []),
            "marking": self.marking,
            "confidence": float(self.confidence or 0.0),
            "status": self.status,
            "raw": self.raw,
        }
