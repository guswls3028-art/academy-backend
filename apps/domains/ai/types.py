from __future__ import annotations

from typing import Any, Dict, Literal, Optional, TypedDict


AIJobType = Literal[
    "ocr",
    "question_segmentation",
    "handwriting_analysis",
    "embedding",
    "problem_generation",
    "homework_video_analysis",
]


class OCRPayload(TypedDict, total=False):
    image_path: str
    engine: Optional[Literal["google", "tesseract", "auto"]]
    academy_id: Optional[int]


class SegmentationPayload(TypedDict, total=False):
    image_path: str
    engine: Optional[Literal["yolo", "opencv", "template", "auto"]]


class HandwritingPayload(TypedDict, total=False):
    image_path: str


class EmbeddingPayload(TypedDict, total=False):
    texts: list[str]
    backend: Optional[Literal["local", "openai", "auto"]]


class ProblemGenerationPayload(TypedDict, total=False):
    ocr_text: str
    model: Optional[str]


class HomeworkVideoPayload(TypedDict, total=False):
    video_path: str
    frame_stride: Optional[int]
    min_frame_count: Optional[int]


def ensure_payload_dict(payload: Any) -> Dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    raise TypeError("payload must be a dict")
