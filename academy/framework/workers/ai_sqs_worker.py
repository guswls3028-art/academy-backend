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

from academy.adapters.db.django.uow import DjangoUnitOfWork
from academy.adapters.queue.sqs.ai_queue import SQSAIQueueAdapter
from academy.adapters.queue.sqs.visibility_extender import SQSVisibilityExtender
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
# Lease 전략: (1) 고정. visibility 3600 - safety_margin 60 = 3540. 문서 §8.3.
LEASE_SECONDS = int(os.getenv("AI_JOB_LEASE_SECONDS", "3540"))
# inference 최대 60분. 초과 시 fail_ai_job + delete + extender stop. 문서 §8.3.
INFERENCE_MAX_SECONDS = int(os.getenv("AI_INFERENCE_MAX_SECONDS", "3600"))

_shutdown = False
_current_receipt_handle: Optional[str] = None


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


def _stop_self_ec2() -> None:
    try:
        import requests
        token = requests.put(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
            timeout=2,
        ).text
        headers = {"X-aws-ec2-metadata-token": token}
        instance_id = requests.get(
            "http://169.254.169.254/latest/meta-data/instance-id",
            headers=headers,
            timeout=2,
        ).text
        region = requests.get(
            "http://169.254.169.254/latest/meta-data/placement/region",
            headers=headers,
            timeout=2,
        ).text
        import boto3
        boto3.client("ec2", region_name=region).stop_instances(InstanceIds=[instance_id])
        logger.info("EC2 self-stop: instance_id=%s (ai worker)", instance_id)
    except Exception as e:
        logger.exception("EC2 self-stop failed (ignored): %s", e)


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
    consecutive_empty = 0

    try:
        while not _shutdown:
            try:
                try:
                    message, tier = _weighted_poll(queue)
                except Exception as e:
                    from libs.queue import QueueUnavailableError
                    if type(e).__name__ == "QueueUnavailableError":
                        logger.warning("SQS unavailable, waiting 60s: %s", e)
                        time.sleep(60)
                        continue
                    raise

                if not message:
                    consecutive_empty += 1
                    consecutive_errors = 0
                    if IDLE_STOP_THRESHOLD > 0 and consecutive_empty >= IDLE_STOP_THRESHOLD:
                        logger.info("Queues empty %d polls, EC2 self-stop in 10s", consecutive_empty)
                        time.sleep(10)  # 500 plan: Dead zone 완화를 위해 Stop 직전 대기
                        logger.info("EC2 self-stop initiating (ai worker)")
                        _stop_self_ec2()
                        return 0
                    continue

                consecutive_empty = 0
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
                        queue.delete(receipt_handle, tier_from_msg)
                        consecutive_errors += 1
                    elif result.status == "DONE":
                        complete_ai_job(uow_factory(), job_id, result.result)
                        queue.delete(receipt_handle, tier_from_msg)
                        logger.info("SQS_JOB_COMPLETED | request_id=%s | job_id=%s", request_id, job_id)
                        consecutive_errors = 0
                    else:
                        fail_ai_job(uow_factory(), job_id, result.error or "failed", tier_from_msg)
                        logger.exception("SQS_JOB_FAILED | request_id=%s | job_id=%s | error=%s", request_id, job_id, result.error)
                        consecutive_errors += 1
                finally:
                    extender.stop()
                    _current_receipt_handle = None

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

        return 0
    finally:
        extender.stop()
        try:
            from django.db import connection
            connection.close()
        except Exception:
            pass


if __name__ == "__main__":
    if os.environ.get("DJANGO_SETTINGS_MODULE"):
        import django
        django.setup()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [AI-SQS-WORKER] %(message)s",
    )
    sys.exit(run_ai_sqs_worker())
