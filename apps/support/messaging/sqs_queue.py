"""
SQS 기반 메시지 발송 큐

API/서비스에서 enqueue → messaging_worker가 소비하여 Solapi로 발송
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

from django.conf import settings
from django.utils import timezone

from libs.queue import get_queue_client

logger = logging.getLogger(__name__)


def _build_business_key(
    tenant_id: int,
    channel: str,
    event_type: str = "manual_send",
    target_type: str = "",
    target_id: str = "",
    recipient: str = "",
    occurrence_key: str = "",
    template_id: str = "",
) -> str:
    """Build SHA-256 business idempotency key from domain fields."""
    canonical = f"msg:{tenant_id}:{channel}:{event_type}:{target_type}:{target_id}:{recipient}:{occurrence_key}:{template_id}"
    return hashlib.sha256(canonical.encode()).hexdigest()


class MessagingSQSQueue:
    """
    메시지 발송 작업 SQS 큐

    메시지 형식:
    {
        "to": str,
        "text": str,
        "sender": str | None,
        "reservation_id": int | None,
        "message_mode": "sms" | "alimtalk",  # sms=SMS만, alimtalk=알림톡만
        "alimtalk_replacements": list[{"key": str, "value": str}] | None,
    }
    """

    # V1 SSOT: academy-v1-messaging-queue
    QUEUE_NAME = "academy-v1-messaging-queue"
    DLQ_NAME = "academy-v1-messaging-queue-dlq"

    def __init__(self):
        self.queue_client = get_queue_client()

    def _get_queue_name(self) -> str:
        return getattr(settings, "MESSAGING_SQS_QUEUE_NAME", self.QUEUE_NAME)

    def enqueue(
        self,
        *,
        tenant_id: int,
        to: str,
        text: str,
        sender: Optional[str] = None,
        reservation_id: Optional[int] = None,
        message_mode: Optional[str] = None,
        alimtalk_replacements: Optional[list[dict]] = None,
        template_id: Optional[str] = None,
        event_type: Optional[str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[int | str] = None,
        occurrence_key: Optional[str] = None,
    ) -> bool:
        """
        발송 작업을 SQS에 추가

        Args:
            tenant_id: 테넌트 ID (워커에서 잔액/PFID 조회용)
            to: 수신 번호
            text: 본문
            sender: 발신 번호
            reservation_id: 예약 ID (워커에서 취소 시 스킵)
            message_mode: "sms" | "alimtalk"
                - sms: SMS만 발송
                - alimtalk: 알림톡만 발송
            alimtalk_replacements: 알림톡 치환 [{"key": "학생이름2", "value": "길동"}, ...]
            template_id: 알림톡 템플릿 ID (미지정 시 워커 기본값 사용)
        """
        mode = (message_mode or "").strip().lower() or "sms"
        if mode not in ("sms", "alimtalk"):
            import logging
            logging.getLogger(__name__).warning(
                "Invalid message_mode '%s' downgraded to 'sms' (tenant=%s, to=%s)",
                message_mode, tenant_id, to,
            )
            mode = "sms"

        message = {
            "tenant_id": int(tenant_id),
            "to": str(to).replace("-", "").strip(),
            "text": (text or "").strip(),
            "sender": (sender or "").strip() or None,
            "created_at": timezone.now().isoformat(),
            "message_mode": mode,
        }
        if reservation_id is not None:
            message["reservation_id"] = int(reservation_id)
        if alimtalk_replacements:
            message["alimtalk_replacements"] = alimtalk_replacements
        if template_id:
            message["template_id"] = str(template_id)
        message["business_idempotency_key"] = _build_business_key(
            tenant_id=int(tenant_id),
            channel=mode,
            event_type=event_type or "manual_send",
            target_type=target_type or "",
            target_id=str(target_id) if target_id else "",
            recipient=message["to"],
            occurrence_key=occurrence_key or timezone.now().strftime("%Y%m%d%H%M%S"),
            template_id=str(template_id) if template_id else "",
        )
        if not message["to"] or not message["text"]:
            logger.warning("enqueue skipped: to or text empty")
            return False
        try:
            ok = self.queue_client.send_message(
                queue_name=self._get_queue_name(),
                message=message,
            )
            if ok:
                logger.info("Messaging job enqueued: to=%s", message["to"][:4] + "****")
            return bool(ok)
        except Exception as e:
            logger.exception("Error enqueuing messaging job: %s", e)
            return False

    def receive_message(self, wait_time_seconds: int = 20) -> Optional[dict]:
        """
        SQS에서 메시지 수신 (Long Polling)

        Returns:
            dict: { to, text, sender, receipt_handle, message_id, created_at } 또는 None
        """
        try:
            raw = self.queue_client.receive_message(
                queue_name=self._get_queue_name(),
                wait_time_seconds=wait_time_seconds,
            )
            if not raw:
                return None
            body = raw.get("Body", "")
            receipt_handle = raw.get("ReceiptHandle")
            if not receipt_handle:
                return None
            if isinstance(body, str):
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    logger.error("Invalid JSON in message body")
                    return None
            else:
                data = body
            if not isinstance(data, dict) or "to" not in data or "text" not in data:
                logger.error("Invalid message format: %s", data)
                return None
            return {
                "to": str(data.get("to", "")),
                "text": str(data.get("text", "")),
                "sender": (data.get("sender") or "").strip() or None,
                "receipt_handle": receipt_handle,
                "message_id": raw.get("MessageId"),
                "created_at": data.get("created_at"),
                "reservation_id": data.get("reservation_id"),
                "alimtalk_replacements": data.get("alimtalk_replacements") or [],
            }
        except Exception as e:
            logger.exception("Error receiving messaging message: %s", e)
            return None

    def delete_message(self, receipt_handle: str) -> bool:
        """처리 완료된 메시지 삭제"""
        try:
            return self.queue_client.delete_message(
                queue_name=self._get_queue_name(),
                receipt_handle=receipt_handle,
            )
        except Exception as e:
            logger.exception("Error deleting message: %s", e)
            return False
