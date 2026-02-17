"""
SQS 기반 메시지 발송 큐

API/서비스에서 enqueue → messaging_worker가 소비하여 Solapi로 발송
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from django.conf import settings
from django.utils import timezone

from libs.queue import get_queue_client

logger = logging.getLogger(__name__)


class MessagingSQSQueue:
    """
    메시지 발송 작업 SQS 큐

    메시지 형식:
    {
        "to": str,
        "text": str,
        "sender": str | None,
        "reservation_id": int | None,
        "message_mode": "sms" | "alimtalk" | "both",  # sms=SMS만, alimtalk=알림톡만, both=알림톡→SMS폴백
        "use_alimtalk_first": bool,  # 하위호환: True면 both, False면 sms
        "alimtalk_replacements": list[{"key": str, "value": str}] | None,
    }
    """

    QUEUE_NAME = "academy-messaging-jobs"
    DLQ_NAME = "academy-messaging-jobs-dlq"

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
        use_alimtalk_first: bool = False,
        alimtalk_replacements: Optional[list[dict]] = None,
        template_id: Optional[str] = None,
    ) -> bool:
        """
        발송 작업을 SQS에 추가

        Args:
            tenant_id: 테넌트 ID (워커에서 잔액/PFID 조회용)
            to: 수신 번호
            text: 본문 (SMS용 또는 알림톡 실패 시 폴백용)
            sender: 발신 번호
            reservation_id: 예약 ID (워커에서 취소 시 스킵)
            message_mode: "sms" | "alimtalk" | "both"
                - sms: SMS만 발송
                - alimtalk: 알림톡만 발송 (실패 시 폴백 없음)
                - both: 알림톡 우선, 실패 시 SMS 폴백
            use_alimtalk_first: (하위호환) True면 both, False면 sms. message_mode가 있으면 무시
            alimtalk_replacements: 알림톡 치환 [{"key": "name", "value": "홍길동"}, ...]
            template_id: 알림톡 템플릿 ID (미지정 시 워커 기본값 사용)
        """
        mode = (message_mode or "").strip().lower() or None
        if not mode:
            mode = "both" if use_alimtalk_first else "sms"
        if mode not in ("sms", "alimtalk", "both"):
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
        if use_alimtalk_first and not message_mode:
            message["use_alimtalk_first"] = True
        if alimtalk_replacements:
            message["alimtalk_replacements"] = alimtalk_replacements
        if template_id:
            message["template_id"] = str(template_id)
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
                "use_alimtalk_first": bool(data.get("use_alimtalk_first")),
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
