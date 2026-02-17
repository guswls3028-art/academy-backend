# apps/support/messaging/services.py
"""
메시지 발송 서비스 — Solapi(SMS/LMS) 연동

- API 키/시크릿: 환경변수 SOLAPI_API_KEY, SOLAPI_API_SECRET (또는 Django 설정)
- 발신번호: SOLAPI_SENDER 또는 settings.SOLAPI_SENDER
"""

import logging
import os
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)


def _get_solapi_credentials() -> tuple[Optional[str], Optional[str]]:
    """Solapi API Key/Secret (환경변수 우선, 설정 fallback). 코드에 키 노출 금지."""
    key = os.environ.get("SOLAPI_API_KEY") or getattr(settings, "SOLAPI_API_KEY", None)
    secret = os.environ.get("SOLAPI_API_SECRET") or getattr(settings, "SOLAPI_API_SECRET", None)
    return (key or None, secret or None)


def _is_mock_mode() -> bool:
    """DEBUG=True 또는 SOLAPI_MOCK=true 이면 실제 API 호출 없이 Mock 사용."""
    if os.environ.get("SOLAPI_MOCK", "").lower() in ("true", "1", "yes"):
        return True
    if getattr(settings, "DEBUG", False):
        return True
    return os.environ.get("DEBUG", "").lower() in ("true", "1", "yes")


def get_solapi_client():
    """
    SolapiMessageService 인스턴스 반환.
    DEBUG=True 또는 SOLAPI_MOCK=true 이면 MockSolapiMessageService (로그만).
    키/시크릿이 없으면 None (스텁 모드).
    """
    if _is_mock_mode():
        from apps.support.messaging.solapi_mock import MockSolapiMessageService
        key, secret = _get_solapi_credentials()
        return MockSolapiMessageService(api_key=key or "", api_secret=secret or "")
    key, secret = _get_solapi_credentials()
    if not key or not secret:
        return None
    try:
        from solapi import SolapiMessageService
        return SolapiMessageService(api_key=key, api_secret=secret)
    except ImportError as e:
        logger.warning("solapi SDK not installed: %s", e)
        return None


def send_sms(
    to: str,
    text: str,
    sender: Optional[str] = None,
) -> dict:
    """
    SMS/LMS 즉시 발송 (Solapi).

    Args:
        to: 수신 번호 (01012345678)
        text: 본문
        sender: 발신 번호 (미지정 시 SOLAPI_SENDER 사용)

    Returns:
        dict: {"status": "ok"|"error"|"skipped", "group_id"?, "reason"?}
    """
    client = get_solapi_client()
    if not client:
        logger.info("send_sms skipped: Solapi not configured")
        return {"status": "skipped", "reason": "solapi_not_configured"}

    sender = (sender or "").strip() or os.environ.get("SOLAPI_SENDER") or getattr(settings, "SOLAPI_SENDER", "")
    if not sender:
        return {"status": "error", "reason": "sender_required"}

    to = (to or "").replace("-", "").strip()
    if not to or not (text or "").strip():
        return {"status": "error", "reason": "to_and_text_required"}

    try:
        from solapi.model import RequestMessage
        message = RequestMessage(from_=sender, to=to, text=text.strip())
        response = client.send(message)
        group_id = getattr(getattr(response, "group_info", None), "group_id", None)
        logger.info("send_sms ok to=%s group_id=%s", to[:4] + "****", group_id)
        return {"status": "ok", "group_id": group_id}
    except Exception as e:
        logger.exception("send_sms failed to=%s", to[:4] + "****")
        return {"status": "error", "reason": str(e)[:500]}


def enqueue_sms(
    tenant_id: int,
    to: str,
    text: str,
    sender: Optional[str] = None,
    *,
    reservation_id: Optional[int] = None,
    message_mode: Optional[str] = None,
    use_alimtalk_first: bool = False,
    alimtalk_replacements: Optional[list[dict]] = None,
    template_id: Optional[str] = None,
) -> bool:
    """
    SMS/알림톡 발송을 SQS에 넣어 워커가 비동기로 발송하도록 함.

    Args:
        tenant_id: 테넌트 ID (워커에서 잔액/PFID 조회)
        to: 수신 번호
        text: 본문 (SMS용 또는 알림톡 실패 시 폴백용)
        sender: 발신 번호
        reservation_id: 예약 ID 있으면 워커에서 취소 여부 Double Check 후 발송/스킵
        message_mode: "sms" | "alimtalk" | "both"
            - sms: SMS만 발송
            - alimtalk: 알림톡만 발송 (실패 시 폴백 없음)
            - both: 알림톡 우선, 실패 시 SMS 폴백
        use_alimtalk_first: (하위호환) True면 both, False면 sms. message_mode가 있으면 무시
        alimtalk_replacements: 알림톡 템플릿 치환 [{"key": "name", "value": "홍길동"}, ...]
        template_id: 알림톡 템플릿 ID (선택)

    Returns:
        bool: enqueue 성공 여부
    """
    from apps.support.messaging.sqs_queue import MessagingSQSQueue
    queue = MessagingSQSQueue()
    return queue.enqueue(
        tenant_id=tenant_id,
        to=to,
        text=text,
        sender=sender,
        reservation_id=reservation_id,
        message_mode=message_mode,
        use_alimtalk_first=use_alimtalk_first,
        alimtalk_replacements=alimtalk_replacements,
        template_id=template_id,
    )


def is_reservation_cancelled(reservation_id: int) -> bool:
    """
    예약 취소 여부 (Double Check용).
    Django ORM이 로드된 상태에서, 프로젝트 내 Reservation 비슷한 모델의 status가 CANCELLED면 True.
    해당 모델이 없거나 status가 다르면 False.
    """
    try:
        from django.apps import apps
        for model in apps.get_models():
            if model.__name__ == "Reservation" and hasattr(model, "status"):
                r = model.objects.filter(pk=reservation_id).first()
                if r and getattr(r, "status", None) == "CANCELLED":
                    return True
        return False
    except Exception:
        return False


def send_clinic_reminder_for_students(*args, **kwargs):
    """
    서버 부팅용 더미 함수
    - 실제 문자 발송 없음
    - ImportError 방지용
    """
    return {
        "status": "noop",
        "message": "clinic reminder skipped (stub)",
    }


def get_site_url(request=None):
    """홈페이지 링크 (메시지용)"""
    from django.conf import settings
    url = getattr(settings, "SITE_URL", None)
    if url:
        return url.rstrip("/")
    if request:
        scheme = "https" if request.is_secure() else "http"
        return f"{scheme}://{request.get_host()}"
    return ""


def send_welcome_messages(
    *,
    created_students: list,
    student_password: str,
    parent_password_by_phone: dict = None,
    site_url: str = "",
):
    """
    가입 성공 메시지 일괄 발송 (학생 + 학부모)

    - 학생용: 홈페이지 링크 + 학생이름, 학생ID, 학생비번
    - 학부모용: 홈페이지 링크 + 학부모ID(학부모폰번호), 학부모비번, 학생이름, 아이디, 비번

    현재는 스텁: 로깅만. 실제 SMS 연동 시 여기서 구현.
    """
    parent_password_by_phone = parent_password_by_phone or {}
    sent = 0

    for student in created_students:
        name = getattr(student, "name", "")
        ps_number = getattr(student, "ps_number", "")
        parent_phone = getattr(student, "parent_phone", "")

        # 학생용 메시지
        student_msg = (
            f"[가입 완료]\n{site_url}\n"
            f"학생이름: {name}\n학생 ID: {ps_number}\n학생 비번: {student_password}"
        )
        logger.info("send_welcome (student) %s: %s", parent_phone or "no-phone", student_msg[:80])
        sent += 1

        # 학부모용 메시지 (학부모 전화번호가 있으면)
        if parent_phone:
            pwd = parent_password_by_phone.get(parent_phone, student_password)
            parent_msg = (
                f"[가입 완료]\n{site_url}\n"
                f"학부모 ID: {parent_phone}\n학부모 비번: {pwd}\n"
                f"학생이름: {name}\n아이디: {ps_number}\n비번: {student_password}"
            )
            logger.info("send_welcome (parent) %s: %s", parent_phone, parent_msg[:80])
            sent += 1

    return {"status": "stub", "logged": sent}
