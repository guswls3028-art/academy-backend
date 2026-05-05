# apps/worker/ai/config.py
from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from typing import Optional, Dict


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v not in (None, "") else default


# YOLO 양식별 conf 매핑 (P1.5, 2026-05-06).
# 양식별 학습 분포 + 운영 검증 기반 default. 학원장 본질 의견("그림 매칭/문항 검출 정밀도")
# + V9 학습 풀 분포 (commercial 1402 / academy 1581 / student 173 / school_exam 3) 반영.
#
# - commercial_workbook (신과함께 26-1m 등): 0.35 — 정밀 인쇄 양식. false positive 제거 우선.
#   페이지당 문항 균질하고 문항 사이 명확 구분 → 높은 conf로도 recall 유지.
# - academy_workbook (메인자료/꾸불한선 등): 0.30 — default. 학교별 양식 다양성 큼 (중간).
# - student_exam_photo (1학기 중간고사 학생 사진): 0.25 — recall 우선. 손글씨 / 사진 노이즈 /
#   조명 변동 → conf 낮춰서 누락 차단. false positive는 사용자 manual cut 보정.
# - school_exam_pdf (학교 시험지 PDF 출제원안): 0.30 — academy 동일 default.
# - other / 미지정: 0.30 default (YOLO_QUESTION_CONF_THRESHOLD 폴백).
#
# ENV YOLO_QUESTION_CONF_MAP 으로 운영 override 가능 (JSON):
#   YOLO_QUESTION_CONF_MAP='{"commercial_workbook":0.4,"student_exam_photo":0.22}'
_DEFAULT_CONF_MAP: Dict[str, float] = {
    "commercial_workbook": 0.35,
    "academy_workbook": 0.30,
    "student_exam_photo": 0.25,
    "school_exam_pdf": 0.30,
}


def _load_conf_map() -> Dict[str, float]:
    raw = os.getenv("YOLO_QUESTION_CONF_MAP")
    if not raw:
        return dict(_DEFAULT_CONF_MAP)
    try:
        m = json.loads(raw)
        if not isinstance(m, dict):
            return dict(_DEFAULT_CONF_MAP)
        out = dict(_DEFAULT_CONF_MAP)
        for k, v in m.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                pass
        return out
    except json.JSONDecodeError:
        return dict(_DEFAULT_CONF_MAP)


@dataclass(frozen=True)
class AIConfig:
    # OCR
    OCR_ENGINE: str = "google"  # google | tesseract | auto
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None  # optional (google sdk default)

    # Segmentation
    QUESTION_SEGMENTATION_ENGINE: str = "auto"  # yolo|opencv|template|auto

    # YOLO (optional)
    YOLO_QUESTION_MODEL_PATH: Optional[str] = None
    YOLO_QUESTION_INPUT_SIZE: int = 1024  # 시험지 A4 해상도에 맞춤 (학습 imgsz 일치)
    YOLO_QUESTION_CONF_THRESHOLD: float = 0.3  # 문항 감지 default — source_type 미지정 시
    YOLO_QUESTION_IOU_THRESHOLD: float = 0.5
    # 양식별 conf 매핑 (P1.5) — segment_questions_yolo(source_type=...) 호출 시 사용.
    YOLO_QUESTION_CONF_MAP: Dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_CONF_MAP))

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
            YOLO_QUESTION_INPUT_SIZE=int(_env("YOLO_QUESTION_INPUT_SIZE", "1024") or "1024"),
            YOLO_QUESTION_CONF_THRESHOLD=float(_env("YOLO_QUESTION_CONF_THRESHOLD", "0.3") or "0.3"),
            YOLO_QUESTION_IOU_THRESHOLD=float(_env("YOLO_QUESTION_IOU_THRESHOLD", "0.5") or "0.5"),
            YOLO_QUESTION_CONF_MAP=_load_conf_map(),

            EMBEDDING_BACKEND=_env("EMBEDDING_BACKEND", "auto") or "auto",
            EMBEDDING_LOCAL_MODEL=_env(
                "EMBEDDING_LOCAL_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            ) or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            EMBEDDING_OPENAI_MODEL=_env("EMBEDDING_OPENAI_MODEL", "text-embedding-3-small") or "text-embedding-3-small",
            OPENAI_API_KEY=_env("OPENAI_API_KEY") or _env("EMBEDDING_OPENAI_API_KEY"),

            PROBLEM_GEN_MODEL=_env("PROBLEM_GEN_MODEL", "gpt-4.1-mini") or "gpt-4.1-mini",
        )
