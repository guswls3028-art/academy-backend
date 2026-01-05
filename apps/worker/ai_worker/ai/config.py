# apps/worker/ai/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v not in (None, "") else default


@dataclass(frozen=True)
class AIConfig:
    # OCR
    OCR_ENGINE: str = "google"  # google | tesseract | auto
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None  # optional (google sdk default)

    # Segmentation
    QUESTION_SEGMENTATION_ENGINE: str = "auto"  # yolo|opencv|template|auto

    # YOLO (optional)
    YOLO_QUESTION_MODEL_PATH: Optional[str] = None
    YOLO_QUESTION_INPUT_SIZE: int = 640
    YOLO_QUESTION_CONF_THRESHOLD: float = 0.4
    YOLO_QUESTION_IOU_THRESHOLD: float = 0.5

    # Embedding
    EMBEDDING_BACKEND: str = "auto"  # local|openai|auto
    EMBEDDING_LOCAL_MODEL: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    EMBEDDING_OPENAI_MODEL: str = "text-embedding-3-small"
    OPENAI_API_KEY: Optional[str] = None

    # Problem generation
    PROBLEM_GEN_MODEL: str = "gpt-4.1-mini"  # default

    @staticmethod
    def load() -> "AIConfig":
        return AIConfig(
            OCR_ENGINE=_env("OCR_ENGINE", "google") or "google",
            GOOGLE_APPLICATION_CREDENTIALS=_env("GOOGLE_APPLICATION_CREDENTIALS"),

            QUESTION_SEGMENTATION_ENGINE=_env("QUESTION_SEGMENTATION_ENGINE", "auto") or "auto",

            YOLO_QUESTION_MODEL_PATH=_env("YOLO_QUESTION_MODEL_PATH"),
            YOLO_QUESTION_INPUT_SIZE=int(_env("YOLO_QUESTION_INPUT_SIZE", "640") or "640"),
            YOLO_QUESTION_CONF_THRESHOLD=float(_env("YOLO_QUESTION_CONF_THRESHOLD", "0.4") or "0.4"),
            YOLO_QUESTION_IOU_THRESHOLD=float(_env("YOLO_QUESTION_IOU_THRESHOLD", "0.5") or "0.5"),

            EMBEDDING_BACKEND=_env("EMBEDDING_BACKEND", "auto") or "auto",
            EMBEDDING_LOCAL_MODEL=_env(
                "EMBEDDING_LOCAL_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            ) or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            EMBEDDING_OPENAI_MODEL=_env("EMBEDDING_OPENAI_MODEL", "text-embedding-3-small") or "text-embedding-3-small",
            OPENAI_API_KEY=_env("OPENAI_API_KEY") or _env("EMBEDDING_OPENAI_API_KEY"),

            PROBLEM_GEN_MODEL=_env("PROBLEM_GEN_MODEL", "gpt-4.1-mini") or "gpt-4.1-mini",
        )
