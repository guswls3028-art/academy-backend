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
    tenant_id: Optional[int] = None,
) -> dict:
    """
    SMS/LMS 즉시 발송 (Solapi).

    Args:
        to: 수신 번호 (01012345678)
        text: 본문
        sender: 발신 번호 (미지정 시 SOLAPI_SENDER 사용)
        tenant_id: 요청 tenant. 지정 시 해당 tenant가 SMS 허용(내 테넌트)인지 검사.

    Returns:
        dict: {"status": "ok"|"error"|"skipped", "group_id"?, "reason"?}
    """
    if tenant_id is not None:
        from apps.support.messaging.policy import can_send_sms, is_messaging_disabled
        if is_messaging_disabled(tenant_id):
            logger.info("send_sms skipped: tenant_id=%s is test tenant (messaging disabled)", tenant_id)
            return {"status": "skipped", "reason": "messaging_disabled_for_test_tenant"}
        if not can_send_sms(tenant_id):
            logger.warning(
                "send_sms blocked by policy: tenant_id=%s is not owner tenant (SMS allowed only for owner)",
                tenant_id,
            )
            return {"status": "error", "reason": "sms_allowed_only_for_owner_tenant"}

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
    from apps.support.messaging.policy import can_send_sms, MessagingPolicyError, is_messaging_disabled

    # 로컬 테스트용 tenant(9999): 알림톡·문자 없이 기능만 동작 (발송 스킵)
    if is_messaging_disabled(tenant_id):
        logger.info("enqueue_sms skipped: tenant_id=%s is test tenant (messaging disabled)", tenant_id)
        return False

    mode = (message_mode or "").strip().lower() or None
    if not mode:
        mode = "both" if use_alimtalk_first else "sms"
    if mode not in ("sms", "alimtalk", "both"):
        mode = "sms"

    # SMS 또는 both(알림톡 실패 시 SMS 폴백)인 경우, 내 테넌트에서만 허용
    if mode in ("sms", "both"):
        if not can_send_sms(tenant_id):
            logger.warning(
                "enqueue_sms blocked by policy: tenant_id=%s cannot send SMS (allowed only for owner tenant)",
                tenant_id,
            )
            raise MessagingPolicyError(
                "문자(SMS) 발송은 내 테넌트에서만 가능합니다.",
                reason="sms_allowed_only_for_owner_tenant",
            )

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


def is_reservation_cancelled(reservation_id: int, tenant_id=None) -> bool:
    """
    예약 취소 여부 (Double Check용).
    tenant_id가 주어지면 해당 테넌트 소속 예약만 조회(격리). 모델에 tenant_id 없으면 tenant_id 무시.
    """
    try:
        from django.apps import apps
        for model in apps.get_models():
            if model.__name__ != "Reservation" or not hasattr(model, "status"):
                continue
            if tenant_id is not None and hasattr(model, "tenant_id"):
                r = model.objects.filter(tenant_id=tenant_id, pk=reservation_id).first()
            else:
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
    가입 성공 메시지 일괄 발송 (학생 + 학부모).

    자동발송 설정(student_signup)이 활성화되어 있고 템플릿이 있으면 enqueue_sms로 발송.
    없으면 로깅만 (기존 스텁 동작).
    """
    parent_password_by_phone = parent_password_by_phone or {}
    sent = 0

    if not created_students:
        return {"status": "skip", "enqueued": 0}

    from apps.support.messaging.selectors import get_auto_send_config
    from apps.support.messaging.policy import MessagingPolicyError

    tenant_id = getattr(created_students[0], "tenant_id", None)
    if not tenant_id:
        logger.warning("send_welcome: no tenant_id, skip")
        return {"status": "skip", "enqueued": 0}

    config = get_auto_send_config(tenant_id, "student_signup")
    if not config or not config.enabled or not config.template:
        for student in created_students:
            name = getattr(student, "name", "")
            logger.info("send_welcome (stub) student=%s", name)
            sent += 1
        return {"status": "stub", "logged": sent}

    t = config.template
    body = (t.body or "").strip()
    subject = (t.subject or "").strip()
    solapi_id = (t.solapi_template_id or "").strip()
    use_alimtalk = config.message_mode in ("alimtalk", "both") and solapi_id and t.solapi_status == "APPROVED"

    for student in created_students:
        name = (getattr(student, "name", "") or "").strip()
        ps_number = (getattr(student, "ps_number", "") or "").strip()
        phone = (getattr(student, "phone", "") or "").replace("-", "").strip()
        parent_phone = (getattr(student, "parent_phone", "") or "").replace("-", "").strip()
        name_2 = name[:2] if len(name) >= 2 else name
        name_3 = name[:3] if len(name) >= 3 else name

        # 학생용
        if phone and len(phone) >= 10:
            text = (
                body.replace("#{student_name_2}", name_2)
                .replace("#{student_name_3}", name_3)
                .replace("#{site_link}", site_url)
                .replace("#{student_id}", ps_number)
                .replace("#{student_password}", student_password)
            )
            if subject:
                text = subject.strip() + "\n" + text
            alimtalk_replacements = None
            template_id_solapi = None
            if use_alimtalk:
                template_id_solapi = solapi_id
                alimtalk_replacements = [
                    {"key": "student_name_2", "value": name_2},
                    {"key": "student_name_3", "value": name_3},
                    {"key": "site_link", "value": site_url},
                    {"key": "student_id", "value": ps_number},
                    {"key": "student_password", "value": student_password},
                ]
            try:
                ok = enqueue_sms(
                    tenant_id=tenant_id,
                    to=phone,
                    text=text,
                    message_mode=config.message_mode,
                    template_id=template_id_solapi,
                    alimtalk_replacements=alimtalk_replacements,
                )
            except MessagingPolicyError:
                logger.info("send_welcome student SMS skipped (policy: tenant_id=%s)", tenant_id)
                ok = False
            if ok:
                sent += 1

        # 학부모용
        if parent_phone and len(parent_phone) >= 10:
            pwd = parent_password_by_phone.get(parent_phone, student_password)
            text = (
                body.replace("#{student_name_2}", name_2)
                .replace("#{student_name_3}", name_3)
                .replace("#{site_link}", site_url)
                .replace("#{student_id}", ps_number)
                .replace("#{student_password}", student_password)
                .replace("#{parent_password}", pwd)
                .replace("#{parent_id}", parent_phone)
            )
            if subject:
                text = subject.strip() + "\n" + text
            alimtalk_replacements = None
            template_id_solapi = None
            if use_alimtalk:
                template_id_solapi = solapi_id
                alimtalk_replacements = [
                    {"key": "student_name_2", "value": name_2},
                    {"key": "student_name_3", "value": name_3},
                    {"key": "site_link", "value": site_url},
                    {"key": "student_id", "value": ps_number},
                    {"key": "student_password", "value": student_password},
                    {"key": "parent_password", "value": pwd},
                    {"key": "parent_id", "value": parent_phone},
                ]
            try:
                ok = enqueue_sms(
                    tenant_id=tenant_id,
                    to=parent_phone,
                    text=text,
                    message_mode=config.message_mode,
                    template_id=template_id_solapi,
                    alimtalk_replacements=alimtalk_replacements,
                )
            except MessagingPolicyError:
                logger.info("send_welcome parent SMS skipped (policy: tenant_id=%s)", tenant_id)
                ok = False
            if ok:
                sent += 1

    return {"status": "enqueued", "enqueued": sent}


# 가입 승인 알림톡용 플레이스홀더
REGISTRATION_APPROVED_NOTICE = "접속해서 ID·비밀번호를 변경할 수 있습니다."


def send_registration_approved_messages(
    *,
    tenant_id: int,
    site_url: str,
    student_name: str,
    student_phone: str,
    student_id: str,
    student_password: str,
    parent_phone: str,
    parent_password: str,
) -> dict:
    """
    가입 신청 승인 시 학생·학부모에게 알림톡/SMS 발송.

    - 학생용: 트리거 registration_approved_student 템플릿 사용
      플레이스홀더: #{student_name}, #{student_id}, #{student_password}, #{site_link}, #{pw_notice}
    - 학부모용: 트리거 registration_approved_parent 템플릿 사용
      플레이스홀더: #{parent_id}, #{parent_password}, #{student_name}, #{student_id}, #{student_password}, #{site_link}, #{pw_notice}

    설정이 없거나 비활성화면 발송하지 않음.
    """
    from apps.support.messaging.selectors import get_auto_send_config
    from apps.support.messaging.policy import MessagingPolicyError

    sent = 0
    student_phone = (student_phone or "").replace("-", "").strip()
    parent_phone = (parent_phone or "").replace("-", "").strip()
    site_url = (site_url or "").strip()
    notice = REGISTRATION_APPROVED_NOTICE

    # 학생용
    config_student = get_auto_send_config(tenant_id, "registration_approved_student")
    if config_student and config_student.template and student_phone and len(student_phone) >= 10:
        t = config_student.template
        body = (t.body or "").strip()
        subject = (t.subject or "").strip()
        solapi_id = (t.solapi_template_id or "").strip()
        use_alimtalk = config_student.message_mode in ("alimtalk", "both") and solapi_id and getattr(t, "solapi_status", None) == "APPROVED"

        text = (
            body.replace("#{student_name}", student_name or "")
            .replace("#{student_id}", student_id or "")
            .replace("#{student_password}", student_password or "")
            .replace("#{site_link}", site_url)
            .replace("#{pw_notice}", notice)
        )
        if subject:
            text = subject + "\n" + text
        alimtalk_replacements = None
        template_id_solapi = None
        if use_alimtalk:
            template_id_solapi = solapi_id
            alimtalk_replacements = [
                {"key": "student_name", "value": student_name or ""},
                {"key": "student_id", "value": student_id or ""},
                {"key": "student_password", "value": student_password or ""},
                {"key": "site_link", "value": site_url},
                {"key": "pw_notice", "value": notice},
            ]
        try:
            if enqueue_sms(
                tenant_id=tenant_id,
                to=student_phone,
                text=text,
                message_mode=config_student.message_mode,
                template_id=template_id_solapi,
                alimtalk_replacements=alimtalk_replacements,
            ):
                sent += 1
        except MessagingPolicyError:
            logger.info("send_registration_approved student skipped (policy: tenant_id=%s)", tenant_id)

    # 학부모용 (학부모 로그인 ID = 전화번호로 통일하여 안내)
    config_parent = get_auto_send_config(tenant_id, "registration_approved_parent")
    if config_parent and config_parent.template and parent_phone and len(parent_phone) >= 10:
        t = config_parent.template
        body = (t.body or "").strip()
        subject = (t.subject or "").strip()
        solapi_id = (t.solapi_template_id or "").strip()
        use_alimtalk = config_parent.message_mode in ("alimtalk", "both") and solapi_id and getattr(t, "solapi_status", None) == "APPROVED"

        parent_id_display = parent_phone  # 로그인 ID로 전화번호 안내
        text = (
            body.replace("#{parent_id}", parent_id_display)
            .replace("#{parent_password}", parent_password or "")
            .replace("#{student_name}", student_name or "")
            .replace("#{student_id}", student_id or "")
            .replace("#{student_password}", student_password or "")
            .replace("#{site_link}", site_url)
            .replace("#{pw_notice}", notice)
        )
        if subject:
            text = subject + "\n" + text
        alimtalk_replacements = None
        template_id_solapi = None
        if use_alimtalk:
            template_id_solapi = solapi_id
            alimtalk_replacements = [
                {"key": "parent_id", "value": parent_id_display},
                {"key": "parent_password", "value": parent_password or ""},
                {"key": "student_name", "value": student_name or ""},
                {"key": "student_id", "value": student_id or ""},
                {"key": "student_password", "value": student_password or ""},
                {"key": "site_link", "value": site_url},
                {"key": "pw_notice", "value": notice},
            ]
        try:
            if enqueue_sms(
                tenant_id=tenant_id,
                to=parent_phone,
                text=text,
                message_mode=config_parent.message_mode,
                template_id=template_id_solapi,
                alimtalk_replacements=alimtalk_replacements,
            ):
                sent += 1
        except MessagingPolicyError:
            logger.info("send_registration_approved parent skipped (policy: tenant_id=%s)", tenant_id)

    if sent:
        return {"status": "enqueued", "enqueued": sent}
    return {"status": "skip", "enqueued": 0}
