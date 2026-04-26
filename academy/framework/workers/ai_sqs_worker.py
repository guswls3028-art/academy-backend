"""
AI SQS Worker — Hexagonal 프레임워크 계층 (thin)

- Use Case + Adapter만 호출.
- SQS 수신 → prepare_ai_job → visibility 연장 → inference → complete/fail → delete.
- 기존 apps.worker.ai_worker.ai.pipelines.dispatcher.handle_ai_job 사용 (inference).
"""
from __future__ import annotations

import logging
import os
import random
import signal
import sys
import threading
import time
import uuid
from typing import Optional

from django.db import close_old_connections, connections

from academy.adapters.db.django.uow import DjangoUnitOfWork
from academy.adapters.queue.sqs.ai_queue import SQSAIQueueAdapter
from academy.adapters.queue.sqs.visibility_extender import SQSVisibilityExtender
from libs.queue import QueueUnavailableError
from academy.application.use_cases.ai.process_ai_job_from_sqs import (
    prepare_ai_job,
    complete_ai_job,
    fail_ai_job,
    PreparedJob,
)

logger = logging.getLogger("academy.ai_sqs_worker")

# 상수 (기존 sqs_main_cpu와 동일)
SQS_WAIT_TIME_SECONDS = 20
SQS_VISIBILITY_TIMEOUT = int(os.getenv("AI_SQS_VISIBILITY_TIMEOUT", "3600"))  # 1시간 연장
VISIBILITY_EXTEND_INTERVAL = int(os.getenv("AI_VISIBILITY_EXTEND_INTERVAL", "60"))  # 60초마다 연장
BASIC_POLL_WEIGHT = int(os.getenv("AI_WORKER_BASIC_POLL_WEIGHT", "3"))
LITE_POLL_WEIGHT = int(os.getenv("AI_WORKER_LITE_POLL_WEIGHT", "1"))
MIN_JOB_INTERVAL_SECONDS = float(os.getenv("AI_WORKER_MIN_JOB_INTERVAL_SECONDS", "1.0"))
# Lease 전략: (1) 고정. visibility 3600 - safety_margin 60 = 3540. 문서 §8.3.
LEASE_SECONDS = int(os.getenv("AI_JOB_LEASE_SECONDS", "3540"))
# inference 최대 60분. 초과 시 fail_ai_job + delete + extender stop. 문서 §8.3.
INFERENCE_MAX_SECONDS = int(os.getenv("AI_INFERENCE_MAX_SECONDS", "3600"))

_shutdown = False
_current_receipt_handle: Optional[str] = None


def _release_db_connections() -> None:
    """SQS worker는 요청/응답 수명주기가 없으므로 작업 경계에서 DB 세션을 명시 반납한다."""
    try:
        connections.close_all()
    except Exception:
        logger.warning("Failed to close DB connections", exc_info=True)


def _handle_signal(sig, frame) -> None:
    global _shutdown, _current_receipt_handle
    logger.info(
        "Received signal, graceful shutdown | current_job=%s",
        "processing" if _current_receipt_handle else "idle",
    )
    _shutdown = True


def _weighted_poll(queue: SQSAIQueueAdapter) -> tuple[Optional[dict], str]:
    if os.environ.get("AI_WORKER_PREMIUM_ONLY") == "1":
        tier = "premium"
    else:
        total = BASIC_POLL_WEIGHT + LITE_POLL_WEIGHT
        tier = "basic" if random.randint(1, total) <= BASIC_POLL_WEIGHT else "lite"
    msg = queue.receive(tier=tier, wait_time_seconds=SQS_WAIT_TIME_SECONDS)
    return msg, tier


def _dispatch_domain_callback(
    prepared: PreparedJob,
    *,
    status: str,
    result_payload: dict | None,
    error: str | None,
) -> None:
    """
    AI Job 완료 후 도메인 콜백 디스패치.
    콜백 실패는 AI Job 상태에 영향을 주지 않는다 (fire-and-forget with logging).
    """
    try:
        from apps.domains.ai.callbacks import dispatch_ai_result_to_domain
        dispatch_ai_result_to_domain(
            job_id=prepared.job_id,
            status=status,
            result_payload=result_payload,
            error=error,
            source_domain=prepared.source_domain,
            source_id=prepared.source_id,
            tier=prepared.tier,
        )
    except Exception:
        logger.exception(
            "Domain callback failed (non-fatal): job_id=%s source=%s/%s",
            prepared.job_id, prepared.source_domain, prepared.source_id,
        )


def _run_inference(prepared: PreparedJob):
    """기존 handle_ai_job 호출 (contract 변환)."""
    from apps.shared.contracts.ai_job import AIJob
    from apps.shared.contracts.ai_result import AIResult
    from apps.worker.ai_worker.ai.pipelines.dispatcher import handle_ai_job
    from apps.worker.ai_worker.ai.pipelines.tier_enforcer import enforce_tier_limits

    allowed, error_msg = enforce_tier_limits(
        tier=prepared.tier,
        job_type=prepared.job_type,
    )
    if not allowed:
        return AIResult.failed(prepared.job_id, error_msg or "tier_limit")

    job_dict = {
        "id": prepared.job_id,
        "type": prepared.job_type or "ocr",
        "tenant_id": prepared.tenant_id,
        "source_domain": prepared.source_domain,
        "source_id": prepared.source_id,
        "payload": prepared.payload,
        "created_at": "",
    }
    job = AIJob.from_dict(job_dict)
    return handle_ai_job(job)


def run_ai_sqs_worker() -> int:
    """메인 루프. 0 정상 종료, 1 오류."""
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    queue = SQSAIQueueAdapter()
    extender = SQSVisibilityExtender(queue)
    uow_factory = DjangoUnitOfWork

    consecutive_errors = 0
    max_consecutive_errors = 10
    last_job_finished_at = 0.0

    try:
        while not _shutdown:
            try:
                # 워커 루프 경계에서 stale DB 커넥션 정리 (누수/반납 지연 보호)
                close_old_connections()
                try:
                    message, tier = _weighted_poll(queue)
                except QueueUnavailableError:
                    logger.warning("SQS unavailable, waiting 60s")
                    time.sleep(60)
                    continue

                if not message:
                    consecutive_errors = 0
                    continue

                # 워커 처리율 상한(초당 1/N)으로 DB 급격한 burst 완화
                if MIN_JOB_INTERVAL_SECONDS > 0 and last_job_finished_at > 0:
                    elapsed = time.time() - last_job_finished_at
                    if elapsed < MIN_JOB_INTERVAL_SECONDS:
                        time.sleep(MIN_JOB_INTERVAL_SECONDS - elapsed)

                receipt_handle = message.get("receipt_handle")
                job_id = message.get("job_id")
                job_type = message.get("job_type") or message.get("type") or message.get("task") or ""
                tier_from_msg = message.get("tier", tier)
                payload = message.get("payload", {})

                if not receipt_handle or not job_id or not job_type:
                    logger.error("Invalid message: job_id=%s receipt_handle=%s", job_id, bool(receipt_handle))
                    if job_id and receipt_handle:
                        queue.delete(receipt_handle, tier_from_msg)
                    continue

                request_id = uuid.uuid4().hex[:8]
                logger.info(
                    "SQS_MESSAGE_RECEIVED | request_id=%s | job_id=%s | tier=%s",
                    request_id, job_id, tier_from_msg,
                )

                global _current_receipt_handle
                _current_receipt_handle = receipt_handle

                # 1) Use Case: prepare (RUNNING 전이)
                prepared = prepare_ai_job(
                    uow_factory(),
                    job_id=job_id,
                    receipt_handle=receipt_handle,
                    tier=tier_from_msg,
                    payload=payload,
                    job_type=job_type,
                    tenant_id=message.get("tenant_id"),
                    source_domain=message.get("source_domain"),
                    source_id=message.get("source_id"),
                    lease_seconds=LEASE_SECONDS,
                )

                if prepared is None:
                    # 이미 완료/실패 등 → 메시지만 삭제 (멱등)
                    queue.delete(receipt_handle, tier_from_msg)
                    logger.info("AI_JOB_IDEMPOTENT_SKIP | job_id=%s", job_id)
                    _current_receipt_handle = None
                    if _shutdown:
                        break
                    continue

                # 2) Visibility 연장 시작 (장시간 작업 대비)
                extender.start(
                    receipt_handle=receipt_handle,
                    tier=tier_from_msg,
                    interval_seconds=VISIBILITY_EXTEND_INTERVAL,
                    visibility_timeout_seconds=SQS_VISIBILITY_TIMEOUT,
                )

                result_container: list = []

                def _run_inference_safe() -> None:
                    try:
                        result_container.append(_run_inference(prepared))
                    except Exception as e:
                        from apps.shared.contracts.ai_result import AIResult
                        result_container.append(AIResult.failed(prepared.job_id, str(e)))
                    finally:
                        # Django DB connections are thread-local. Inference runs in its own
                        # thread, so close from inside that thread too.
                        _release_db_connections()

                try:
                    # 3) Inference (60분 상한. 문서 §8.3)
                    inference_thread = threading.Thread(target=_run_inference_safe, daemon=True)
                    inference_thread.start()
                    inference_thread.join(timeout=INFERENCE_MAX_SECONDS)

                    if inference_thread.is_alive():
                        logger.error(
                            "SQS_JOB_TIMEOUT_60MIN | request_id=%s | job_id=%s | stopping extender",
                            request_id, job_id,
                        )
                        fail_ai_job(uow_factory(), job_id, "inference_timeout_60min", tier_from_msg)
                        _dispatch_domain_callback(
                            prepared, status="FAILED", result_payload=None,
                            error="inference_timeout_60min",
                        )
                        queue.delete(receipt_handle, tier_from_msg)
                        extender.stop()
                        _current_receipt_handle = None
                        consecutive_errors += 1
                        if _shutdown:
                            break
                        continue

                    result = result_container[0] if result_container else None
                    if result is None:
                        fail_ai_job(uow_factory(), job_id, "inference_error_no_result", tier_from_msg)
                        _dispatch_domain_callback(
                            prepared, status="FAILED", result_payload=None,
                            error="inference_error_no_result",
                        )
                        queue.delete(receipt_handle, tier_from_msg)
                        consecutive_errors += 1
                    elif result.status == "DONE":
                        complete_ai_job(uow_factory(), job_id, result.result)
                        _dispatch_domain_callback(
                            prepared, status="DONE",
                            result_payload=result.result if isinstance(result.result, dict) else {},
                            error=None,
                        )
                        queue.delete(receipt_handle, tier_from_msg)
                        logger.info("SQS_JOB_COMPLETED | request_id=%s | job_id=%s", request_id, job_id)
                        consecutive_errors = 0
                    else:
                        fail_ai_job(uow_factory(), job_id, result.error or "failed", tier_from_msg)
                        _dispatch_domain_callback(
                            prepared, status="FAILED", result_payload=None,
                            error=result.error or "failed",
                        )
                        logger.warning("SQS_JOB_FAILED | request_id=%s | job_id=%s | error=%s", request_id, job_id, result.error)
                        consecutive_errors += 1
                finally:
                    extender.stop()
                    _current_receipt_handle = None
                    last_job_finished_at = time.time()

                if consecutive_errors >= max_consecutive_errors:
                    logger.error("Too many consecutive errors (%s), exit", consecutive_errors)
                    return 1
                if _shutdown:
                    break

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.exception("Unexpected error: %s", e)
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    return 1
                time.sleep(5)
            finally:
                # 작업 1건 처리 종료 후 커넥션을 RDS에 즉시 반납한다.
                # close_old_connections()는 정상 persistent connection을 유지할 수 있어
                # 대량 OCR/매치업 배치에서 connection slot 고갈을 막기엔 부족하다.
                _release_db_connections()

        return 0
    finally:
        extender.stop()
        _release_db_connections()


if __name__ == "__main__":
    if os.environ.get("DJANGO_SETTINGS_MODULE"):
        import django
        django.setup()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [AI-SQS-WORKER] %(message)s",
    )
    sys.exit(run_ai_sqs_worker())
