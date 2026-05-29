"""
AI worker → backend OMR callback payload 정본 schema.

이 모듈이 worker가 보낸 raw dict를 typed 객체로 변환·검증한다. 핵심 invariant:

1. 알 수 없는 worker version 은 즉시 reject 한다 (silent acceptance 금지).
2. 필수 key 누락은 ValidationError 로 표면화한다 (silent skip 금지).
3. extra field 는 forbid 한다 (워커가 새 데이터 보내면 호환성 결정 강제).

이전에는 apply_omr_ai_result 가 dict.get() 으로 nullable 접근만 하다가, 새 워커
스키마가 들어와도 silent miss 가 났다. 이 schema 가 worker contract 의 SSOT 다.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


# ──────────────────────────────────────────────────────────────────────────────
# Allowed worker versions
# ──────────────────────────────────────────────────────────────────────────────
# 새 워커 버전을 prod 에 배포하려면 반드시 이 목록에 추가하고 backend 도 같이
# 배포한다. version 검증을 우회한 silent compatibility 는 금지한다.
SUPPORTED_WORKER_VERSIONS: frozenset[str] = frozenset({
    "v10",
    "v10.1",
    "v11",
    "v12",
    "v13",
    "v14",
    "v15",
    "v15.1",
    "v15.2",
})


class OMRPipelineStatus(str, Enum):
    """워커가 보고하는 최상위 처리 상태."""

    DONE = "DONE"
    FAILED = "FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED_BAD_INPUT = "REJECTED_BAD_INPUT"


class OMRAnswerStatus(str, Enum):
    """문항별 인식 상태."""

    OK = "ok"
    BLANK = "blank"
    MULTI = "multi"
    AMBIGUOUS = "ambiguous"
    ERROR = "error"


class OMRBubbleRect(BaseModel):
    """원본/aligned 이미지 픽셀 기준 버블 bbox."""

    model_config = ConfigDict(extra="ignore")

    label: Optional[str] = None
    x: float
    y: float
    w: float
    h: float


class OMRDetectedAnswer(BaseModel):
    """문항 한 개의 worker 인식 결과."""

    model_config = ConfigDict(extra="ignore")

    # 워커는 question_number (1,2,3...) 를 'question_id' 로 보낸다.
    # backend 가 question_number → ExamQuestion.id 로 다시 매핑한다.
    question_id: int = Field(..., ge=1, description="문항 번호 (ExamQuestion PK 아님)")
    detected: list[str] = Field(default_factory=list)
    status: OMRAnswerStatus
    marking: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    raw: Optional[dict[str, Any]] = None
    version: Optional[str] = None

    @field_validator("detected", mode="before")
    @classmethod
    def _normalize_detected(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("detected must be a list")
        return [str(v).strip() for v in value if str(v).strip()]


class OMRIdentifierDigit(BaseModel):
    """식별번호 한 자리에 대한 worker 후보 풀."""

    model_config = ConfigDict(extra="ignore")

    digit_index: int = Field(..., ge=0, le=7)
    value: Optional[int] = Field(default=None, ge=0, le=9)
    status: OMRAnswerStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    z_score: Optional[float] = None
    z_gap: Optional[float] = None
    marks: list[dict[str, Any]] = Field(default_factory=list)


class OMRIdentifierResult(BaseModel):
    """식별번호 8자리 인식 결과."""

    model_config = ConfigDict(extra="ignore")

    # 워커가 표준화한 8자리 (또는 '?' 포함). 매칭 SSOT.
    identifier: Optional[str] = Field(default=None, max_length=16)
    raw_identifier: Optional[str] = Field(default=None, max_length=16)
    status: OMRAnswerStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    digits: list[OMRIdentifierDigit] = Field(default_factory=list)


class OMRImageSize(BaseModel):
    model_config = ConfigDict(extra="ignore")

    width: int = Field(..., ge=1)
    height: int = Field(..., ge=1)


class OMRWorkerResult(BaseModel):
    """worker result 본문 (callback payload 의 'result' 필드)."""

    model_config = ConfigDict(extra="ignore")

    aligned: bool = False
    alignment_method: Optional[str] = None
    alignment_orientation: Optional[int] = None
    aligned_image_key: Optional[str] = None
    aligned_image_size: Optional[OMRImageSize] = None
    answers: list[OMRDetectedAnswer] = Field(default_factory=list)
    identifier: Optional[OMRIdentifierResult] = None
    mode: Optional[str] = None
    input: Optional[dict[str, Any]] = None
    job_id: Optional[str] = None


class OMRWorkerCallback(BaseModel):
    """
    backend 가 받는 worker callback payload 정본.

    callbacks.py 가 raw dict 를 받아 이 model 로 변환한다. 변환 실패는 곧
    worker contract 위반 → 자동 채점 거부 + manual_review 강제.

    extra='ignore' 인 이유: prod 가 받는 raw payload 의 상위 키 목록이 아직
    레거시 키와 섞여 있어서 forbid 로 가면 정상 callback 도 reject 된다. Phase C
    에서 ingest 진입점을 정리한 후 forbid 로 조인다 (TODO #14).
    """

    model_config = ConfigDict(extra="ignore")

    submission_id: int = Field(..., ge=1)
    job_id: Optional[str] = None
    status: OMRPipelineStatus
    error: Optional[str] = None
    tenant_id: Optional[str] = None
    kind: Optional[str] = None
    received_at: Optional[str] = None
    version: Optional[str] = Field(
        default=None,
        description="worker 버전 (v15, v15.2 등). SUPPORTED_WORKER_VERSIONS 안에 있어야 함.",
    )
    result: Optional[OMRWorkerResult] = None

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value not in SUPPORTED_WORKER_VERSIONS:
            raise ValueError(
                f"unsupported worker version {value!r}; "
                f"add to SUPPORTED_WORKER_VERSIONS after compatibility review"
            )
        return value


# callback envelope 의 최상위 키. 그 외 키는 result 본문에 속하는 것으로 본다.
_ENVELOPE_KEYS: frozenset[str] = frozenset({
    "submission_id",
    "job_id",
    "status",
    "error",
    "tenant_id",
    "kind",
    "received_at",
    "version",
    "result",
})


def parse_worker_callback(payload: dict[str, Any]) -> tuple[Optional[OMRWorkerCallback], Optional[str]]:
    """
    Raw dict 를 OMRWorkerCallback 으로 변환.

    정규화:
    - worker 가 result 본문(aligned/identifier/answers 등)을 최상위에 박은 legacy
      format 도 result 키로 모아 정본 형태로 통일한다. apply_omr_ai_result 에
      흩어져있던 fallback 로직을 여기로 단일화.
    - version 이 최상위에 없으면 result.answers[0].version 에서 추론한다.

    Returns:
        (callback, None)  성공 시
        (None, err_msg)  schema 위반 시 (silent skip 금지 — caller 가 manual_review 강제)
    """
    if not isinstance(payload, dict):
        return None, "callback payload is not a dict"

    normalized = dict(payload)

    # tenant_id: caller (callbacks / 테스트) 가 int 로 보내도 str 로 통일.
    tid = normalized.get("tenant_id")
    if isinstance(tid, int):
        normalized["tenant_id"] = str(tid)

    # legacy: result 본문이 최상위에 펼쳐져 있으면 result 로 모은다.
    if not isinstance(normalized.get("result"), dict):
        body_keys = [k for k in normalized.keys() if k not in _ENVELOPE_KEYS]
        if body_keys:
            body: dict[str, Any] = {k: normalized.pop(k) for k in body_keys}
            normalized["result"] = body

    # version 추론: 최상위 → result.version → result.answers[0].version
    if not normalized.get("version"):
        inner = normalized.get("result")
        if isinstance(inner, dict):
            inner_version = inner.get("version")
            if isinstance(inner_version, str) and inner_version:
                normalized["version"] = inner_version
            else:
                inner_answers = inner.get("answers")
                if isinstance(inner_answers, list) and inner_answers:
                    first = inner_answers[0]
                    if isinstance(first, dict) and first.get("version"):
                        normalized["version"] = first["version"]

    try:
        return OMRWorkerCallback.model_validate(normalized), None
    except ValidationError as exc:
        return None, str(exc)
