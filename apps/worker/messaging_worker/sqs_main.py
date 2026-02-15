"""
Messaging Worker - SQS 기반 메시지 발송

SQS academy-messaging-jobs 에서 수신 → Solapi SMS/LMS 발송
video_worker sqs_main 과 동일한 패턴 (Long Polling, Graceful shutdown)
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from typing import Optional

from libs.queue import get_queue_client, QueueUnavailableError
from libs.redis.idempotency import acquire_job_lock, release_job_lock

from apps.worker.messaging_worker.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [MESSAGING-WORKER] %(message)s",
)
logger = logging.getLogger("messaging_worker")

_shutdown = False
_current_receipt_handle: Optional[str] = None


def _handle_signal(sig, frame):
    global _shutdown, _current_receipt_handle
    logger.info(
        "Received signal, initiating graceful shutdown... | current_job=%s",
        "processing" if _current_receipt_handle else "idle",
    )
    _shutdown = True


def _get_solapi_client(cfg):
    """DEBUG=True 또는 SOLAPI_MOCK=true 이면 Mock (로그만), 아니면 실제 Solapi."""
    if os.environ.get("SOLAPI_MOCK", "").lower() in ("true", "1", "yes") or os.environ.get("DEBUG", "").lower() in ("true", "1", "yes"):
        from apps.support.messaging.solapi_mock import MockSolapiMessageService
        return MockSolapiMessageService(api_key=cfg.SOLAPI_API_KEY, api_secret=cfg.SOLAPI_API_SECRET)
    from solapi import SolapiMessageService
    return SolapiMessageService(api_key=cfg.SOLAPI_API_KEY, api_secret=cfg.SOLAPI_API_SECRET)


def send_one_alimtalk(
    cfg,
    to: str,
    sender: str,
    pf_id: str,
    template_id: str,
    replacements: Optional[list] = None,
) -> dict:
    """
    Solapi 알림톡 1건 발송. 실패/수신거부 시 caller가 SMS로 fallback.
    replacements: [{"key": "name", "value": "홍길동"}, ...] — 템플릿 #{name}, #{date}, #{clinic_name} 등 치환.
    """
    try:
        from solapi.model import RequestMessage
        from solapi.model.kakao.kakao_option import KakaoOption
    except ImportError:
        return {"status": "error", "reason": "solapi_not_installed"}
    client = _get_solapi_client(cfg)
    to = (to or "").replace("-", "").strip()
    if not to or not pf_id or not template_id:
        return {"status": "error", "reason": "to_pf_template_required"}
    try:
        kakao_option = KakaoOption(pf_id=pf_id, template_id=template_id)
        message = RequestMessage(
            from_=sender,
            to=to,
            kakao_options=kakao_option,
            replacements=replacements or None,
        )
        response = client.send(message)
        group_id = getattr(getattr(response, "group_info", None), "group_id", None)
        count = getattr(getattr(response, "group_info", None), "count", None)
        if count is not None and getattr(count, "registered_success", 0) == 0:
            reason = "alimtalk_failed_or_rejected"
            logger.warning("alimtalk no success to=%s****", to[:4])
            return {"status": "error", "reason": reason, "group_id": group_id}
        logger.info("send_alimtalk ok to=%s**** group_id=%s", to[:4], group_id)
        return {"status": "ok", "group_id": group_id}
    except Exception as e:
        logger.warning("alimtalk failed to=%s****: %s", to[:4], e)
        return {"status": "error", "reason": str(e)[:500]}


def send_one_sms(cfg, to: str, text: str, sender: str) -> dict:
    """
    Solapi로 SMS 1건 발송.
    Returns: {"status": "ok"|"error", "group_id"?, "reason"?}
    """
    try:
        from solapi.model import RequestMessage
    except ImportError as e:
        logger.error("solapi SDK not installed: %s", e)
        return {"status": "error", "reason": "solapi_not_installed"}
    client = _get_solapi_client(cfg)
    sender = (sender or cfg.SOLAPI_SENDER or "").strip()
    if not sender:
        return {"status": "error", "reason": "sender_required"}

    to = (to or "").replace("-", "").strip()
    text = (text or "").strip()
    if not to or not text:
        return {"status": "error", "reason": "to_and_text_required"}

    try:
        message = RequestMessage(from_=sender, to=to, text=text)
        response = client.send(message)
        group_id = getattr(getattr(response, "group_info", None), "group_id", None)
        # 발송 로그 테이블이 있으면 group_id(및 messageId) 저장 권장 → "문자 안 왔어요" 민원 시 Solapi 콘솔 조회용
        logger.info("send_sms ok to=%s**** group_id=%s", to[:4], group_id)
        return {"status": "ok", "group_id": group_id}
    except Exception as e:
        logger.exception("send_sms failed to=%s****", to[:4])
        return {"status": "error", "reason": str(e)[:500]}


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Django context: 예약/유저 등 DB 조회가 필요할 때 ORM 사용 가능하도록
    if os.environ.get("DJANGO_SETTINGS_MODULE"):
        import django
        django.setup()
        logger.info("Django setup done (ORM available)")

    cfg = load_config()
    queue_client = get_queue_client()

    # Long Polling 10~20초: 빈 큐에 반복 요청 방지 → AWS 비용·CPU 절약
    logger.info(
        "Messaging Worker started | queue=%s | wait_time=%ss",
        cfg.MESSAGING_SQS_QUEUE_NAME,
        cfg.SQS_WAIT_TIME_SECONDS,
    )

    consecutive_errors = 0
    max_consecutive_errors = 10

    try:
        while not _shutdown:
            try:
                try:
                    raw = queue_client.receive_message(
                        queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                        wait_time_seconds=cfg.SQS_WAIT_TIME_SECONDS,
                    )
                except QueueUnavailableError as e:
                    logger.warning(
                        "SQS unavailable (AWS credentials invalid or missing?). Waiting 60s. %s",
                        e,
                    )
                    time.sleep(60)
                    continue
                if not raw:
                    continue

                body = raw.get("Body", "")
                receipt_handle = raw.get("ReceiptHandle")
                message_id = raw.get("MessageId") or receipt_handle
                if not receipt_handle:
                    logger.error("Message missing ReceiptHandle")
                    continue

                job_id = f"messaging:{message_id}"
                if not acquire_job_lock(job_id):
                    queue_client.delete_message(
                        queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                        receipt_handle=receipt_handle,
                    )
                    continue

                global _current_receipt_handle
                try:
                    if isinstance(body, str):
                        try:
                            data = json.loads(body)
                        except json.JSONDecodeError:
                            logger.error("Invalid JSON in message body")
                            queue_client.delete_message(
                                queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                                receipt_handle=receipt_handle,
                            )
                            continue
                    else:
                        data = body

                    if not isinstance(data, dict) or "to" not in data or "text" not in data:
                        logger.error("Invalid message format: %s", data)
                        queue_client.delete_message(
                            queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                            receipt_handle=receipt_handle,
                        )
                        continue

                    tenant_id = data.get("tenant_id")
                    if tenant_id is None and os.environ.get("DJANGO_SETTINGS_MODULE"):
                        logger.warning("Message missing tenant_id, skipping (legacy message)")
                        queue_client.delete_message(
                            queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                            receipt_handle=receipt_handle,
                        )
                        continue

                    # 예약 취소 Double Check: 발송 직전 한 번 더 확인
                    reservation_id = data.get("reservation_id")
                    if reservation_id is not None and os.environ.get("DJANGO_SETTINGS_MODULE"):
                        try:
                            from apps.support.messaging.services import is_reservation_cancelled
                            if is_reservation_cancelled(int(reservation_id)):
                                logger.info("reservation_id=%s cancelled, skip send", reservation_id)
                                queue_client.delete_message(
                                    queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                                    receipt_handle=receipt_handle,
                                )
                                _current_receipt_handle = None
                                continue
                        except Exception as e:
                            logger.warning("reservation check failed: %s", e)

                    _current_receipt_handle = receipt_handle

                    to = str(data.get("to", ""))
                    text = str(data.get("text", ""))
                    sender = (data.get("sender") or "").strip() or cfg.SOLAPI_SENDER
                    use_alimtalk_first = bool(data.get("use_alimtalk_first"))
                    alimtalk_replacements = data.get("alimtalk_replacements") or []
                    template_id_msg = data.get("template_id") or ""

                    # 테넌트별 잔액·PFID·단가 (Django 있을 때만)
                    info = None
                    base_price = "0"
                    pf_id_tenant = ""
                    if tenant_id is not None and os.environ.get("DJANGO_SETTINGS_MODULE"):
                        try:
                            from apps.support.messaging.credit_services import (
                                get_tenant_messaging_info,
                                deduct_credits,
                                rollback_credits,
                            )
                            from apps.support.messaging.models import NotificationLog
                            from apps.core.models import Tenant
                            info = get_tenant_messaging_info(int(tenant_id))
                            if info:
                                base_price = info["base_price"]
                                pf_id_tenant = (info["kakao_pfid"] or "").strip()
                        except Exception as e:
                            logger.warning("get_tenant_messaging_info failed: %s", e)

                    # 알림톡 사용 시: 테넌트 PFID 또는 워커 기본 PFID
                    pf_id = pf_id_tenant or cfg.SOLAPI_KAKAO_PF_ID
                    template_id = (template_id_msg or "").strip() or cfg.SOLAPI_KAKAO_TEMPLATE_ID

                    # 잔액 검증 및 차감 (Django + info 있을 때, 단가 > 0)
                    deducted = False
                    try:
                        if info and float(base_price) > 0 and tenant_id is not None:
                            from decimal import Decimal
                            from apps.support.messaging.credit_services import deduct_credits
                            from academy.adapters.db.django.repositories_messaging import create_notification_log
                            bal = info.get("credit_balance", "0")
                            if float(bal) < float(base_price):
                                logger.warning(
                                    "tenant_id=%s insufficient_balance balance=%s base_price=%s, skip send",
                                    tenant_id, bal, base_price,
                                )
                                create_notification_log(
                                    tenant_id=int(tenant_id),
                                    success=False,
                                    amount_deducted=Decimal("0"),
                                    recipient_summary=to[:4] + "****",
                                    failure_reason="insufficient_balance",
                                )
                                queue_client.delete_message(
                                    queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                                    receipt_handle=receipt_handle,
                                )
                                _current_receipt_handle = None
                                continue
                            deduct_credits(int(tenant_id), base_price)
                            deducted = True
                    except Exception as e:
                        logger.exception("deduct_credits failed: %s", e)
                        _current_receipt_handle = None
                        consecutive_errors += 1
                        continue

                    # 알림톡 → SMS 폴백: 알림톡 우선 시도, 실패 시 즉시 SMS
                    result = None
                    if use_alimtalk_first and pf_id and template_id:
                        result = send_one_alimtalk(
                            cfg, to=to, sender=sender,
                            pf_id=pf_id,
                            template_id=template_id,
                            replacements=alimtalk_replacements if isinstance(alimtalk_replacements, list) else None,
                        )
                        if result.get("status") != "ok":
                            logger.info("alimtalk failed, fallback to SMS")
                            result = send_one_sms(cfg, to=to, text=text, sender=sender)
                    else:
                        result = send_one_sms(cfg, to=to, text=text, sender=sender)

                    # 성공 시 로그, 실패 시 롤백 + 로그
                    if tenant_id is not None and os.environ.get("DJANGO_SETTINGS_MODULE") and info:
                        try:
                            from decimal import Decimal
                            from apps.support.messaging.credit_services import rollback_credits
                            from academy.adapters.db.django.repositories_messaging import create_notification_log
                            if result.get("status") == "ok":
                                create_notification_log(
                                    tenant_id=int(tenant_id),
                                    success=True,
                                    amount_deducted=Decimal(str(base_price)),
                                    recipient_summary=to[:4] + "****",
                                    template_summary=template_id or "SMS",
                                )
                            else:
                                if deducted:
                                    rollback_credits(int(tenant_id), base_price)
                                create_notification_log(
                                    tenant_id=int(tenant_id),
                                    success=False,
                                    amount_deducted=Decimal("0"),
                                    recipient_summary=to[:4] + "****",
                                    failure_reason=result.get("reason", "send_failed")[:500],
                                )
                        except Exception as e:
                            logger.exception("NotificationLog/rollback failed: %s", e)
                            if deducted and result.get("status") != "ok":
                                try:
                                    rollback_credits(int(tenant_id), base_price)
                                except Exception:
                                    pass

                    if result.get("status") == "ok":
                        queue_client.delete_message(
                            queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                            receipt_handle=receipt_handle,
                        )
                        consecutive_errors = 0
                    else:
                        logger.warning("send failed, message will retry: %s", result.get("reason"))
                        consecutive_errors += 1
                        if consecutive_errors >= max_consecutive_errors:
                            logger.error("Too many consecutive errors (%s), exiting", consecutive_errors)
                            return 1

                    _current_receipt_handle = None

                    if _shutdown:
                        logger.info("Graceful shutdown: exiting")
                        break
                finally:
                    release_job_lock(job_id)

            except KeyboardInterrupt:
                break
            except QueueUnavailableError:
                # 이미 내부 try에서 처리하지만, 다른 경로로 올 수 있음
                time.sleep(60)
                continue
            except Exception as e:
                logger.exception("Unexpected error in main loop: %s", e)
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    return 1
                time.sleep(5)

        logger.info("Messaging Worker shutdown complete")
        return 0

    except Exception:
        logger.exception("Fatal error in Messaging Worker")
        return 1


if __name__ == "__main__":
    sys.exit(main())
