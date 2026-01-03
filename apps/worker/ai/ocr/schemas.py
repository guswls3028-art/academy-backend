from __future__ import annotations
from typing import TypedDict, List, Optional


class OMRDetectedAnswer(TypedDict):
    question_number: int
    detected: List[str]
    confidence: float
    marking: str     # single / multi / blank
    status: str      # ok / error


class OCRResultPayload(TypedDict):
    version: str
    answers: List[OMRDetectedAnswer]
    raw_text: Optional[str]
