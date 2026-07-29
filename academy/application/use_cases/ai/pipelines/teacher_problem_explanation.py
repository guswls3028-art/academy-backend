from __future__ import annotations

import logging
from pathlib import Path

from academy.adapters.ai.config import AIConfig
from academy.adapters.ai.problem.generator import generate_transcribed_explanations
from academy.adapters.ai.problem.transcriber import transcribe_problem_image
from academy.adapters.ai.storage.downloader import (
    cleanup_tmp_for_path,
    download_r2_key_to_tmp,
)
from apps.domains.ai.services.quota import consume_ai_quota
from apps.infrastructure.storage.r2 import delete_object_r2_storage
from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult


logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 12 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def handle_teacher_problem_explanation_job(job: AIJob) -> AIResult:
    payload = job.payload if isinstance(job.payload, dict) else {}
    tenant_id = str(job.tenant_id or "").strip()
    payload_tenant_id = str(payload.get("tenant_id") or "").strip()
    request_user_id = str(payload.get("request_user_id") or "").strip()
    source_image_key = str(payload.get("source_image_key") or "").strip()
    content_type = str(payload.get("content_type") or "").strip().lower()
    expected_prefix = f"tenants/{tenant_id}/tools/problem-solver/tmp/"

    if not tenant_id or payload_tenant_id != tenant_id:
        return AIResult.failed(job.id, "tenant_id mismatch")
    if not request_user_id:
        return AIResult.failed(job.id, "request_user_id is required")
    if not source_image_key.startswith(expected_prefix):
        return AIResult.failed(job.id, "invalid source image key")
    if content_type not in ALLOWED_CONTENT_TYPES:
        return AIResult.failed(job.id, "invalid content type")

    local_path: str | None = None
    try:
        local_path = download_r2_key_to_tmp(
            r2_key=source_image_key,
            job_id=job.id,
        )
        if Path(local_path).stat().st_size > MAX_IMAGE_BYTES:
            return AIResult.failed(job.id, "source image is too large")

        image_bytes = Path(local_path).read_bytes()
        consume_ai_quota(kind="problem_studio_transcription")
        config = AIConfig.load()
        transcription = transcribe_problem_image(
            image_bytes,
            mime=content_type,
            api_key=config.OPENAI_API_KEY or "",
            model=config.PROBLEM_TRANSCRIPTION_MODEL,
            bedrock_model=config.PROBLEM_TRANSCRIPTION_BEDROCK_MODEL,
            bedrock_region=config.BEDROCK_REGION,
        )
        if not transcription:
            return AIResult.failed(job.id, "problem transcription was empty")

        explanations = generate_transcribed_explanations(
            questions=[{
                "prompt": transcription,
                "choices": [],
                "answer": "",
                "explanation": "",
            }],
            subject=str(payload.get("subject") or "")[:40],
            note_policy=(
                "정답 근거를 단계별로 설명하고, 대표 오답이 왜 틀렸는지 "
                "강사가 검수하기 쉽게 분리해서 씁니다."
            ),
        )
        if not explanations:
            return AIResult.failed(job.id, "problem explanation was empty")

        draft = explanations[0]
        return AIResult.done(job.id, {
            "answer": str(draft.get("answer") or "검수 필요")[:1000],
            "explanation": str(draft.get("explanation") or "")[:12000],
            "answer_check": str(draft.get("answer_check") or "")[:2000],
            "confidence": (
                str(draft.get("confidence") or "low")
                if str(draft.get("confidence") or "low") in {"high", "medium", "low"}
                else "low"
            ),
            "review_status": "teacher_review_required",
            "subject": str(payload.get("subject") or "")[:40],
        })
    except Exception:
        logger.exception(
            "Teacher problem explanation failed: job_id=%s tenant_id=%s",
            job.id,
            tenant_id,
        )
        return AIResult.failed(job.id, "teacher problem explanation failed")
    finally:
        cleanup_tmp_for_path(local_path)
        try:
            delete_object_r2_storage(key=source_image_key)
        except Exception:
            logger.warning(
                "Teacher problem source cleanup failed: job_id=%s tenant_id=%s",
                job.id,
                tenant_id,
                exc_info=True,
            )
