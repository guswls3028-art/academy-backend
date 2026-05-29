"""
OMR pipeline 의 typed contracts.

AI worker → backend callback 의 payload 정본 schema (worker_response.py),
허용 worker version 목록 (version.py).
"""
from apps.domains.submissions.omr_pipeline.contracts.worker_response import (
    OMRAnswerStatus,
    OMRBubbleRect,
    OMRDetectedAnswer,
    OMRIdentifierDigit,
    OMRIdentifierResult,
    OMRPipelineStatus,
    OMRWorkerCallback,
    OMRWorkerResult,
    parse_worker_callback,
)

__all__ = [
    "OMRAnswerStatus",
    "OMRBubbleRect",
    "OMRDetectedAnswer",
    "OMRIdentifierDigit",
    "OMRIdentifierResult",
    "OMRPipelineStatus",
    "OMRWorkerCallback",
    "OMRWorkerResult",
    "parse_worker_callback",
]
