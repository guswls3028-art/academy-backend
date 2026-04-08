# apps/support/messaging/policy.py
"""
메시징 발송 정책 및 채널 resolver — 단일 진입점.

- SMS: OWNER_TENANT_ID(내 테넌트) 또는 자체 연동 키가 있는 테넌트에서 허용.
- 알림톡: 모든 tenant 허용. tenant별 kakao_pfid 있으면 해당 채널, 없으면 시스템 기본 채널.
"""

import logging
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# 트리거 정책 분류 — SSOT
# ──────────────────────────────────────────
# SYSTEM_AUTO: 시스템 필수 안내. 항상 자동. 사용자가 끌 수 없음.
# AUTO_DEFAULT: 자동 기본값. 사용자가 설정에서 끌 수 있음.
# MANUAL_DEFAULT: 수동 기본값. preview → confirm 필요. 사용자가 자동화 가능.
# DISABLED: 현재 비활성. 정책상 의미 없는 트리거.

TRIGGER_POLICY = {
    # SYSTEM_AUTO — 시스템 필수 안내 메시지
    "registration_approved_student": "SYSTEM_AUTO",
    "registration_approved_parent": "SYSTEM_AUTO",
    "password_find_otp": "SYSTEM_AUTO",
    "password_reset_student": "SYSTEM_AUTO",
    "password_reset_parent": "SYSTEM_AUTO",

    # AUTO_DEFAULT — 학생 행동에 대한 즉시 통보 (클리닉)
    "clinic_reservation_created": "AUTO_DEFAULT",
    "clinic_reservation_changed": "AUTO_DEFAULT",
    "clinic_cancelled": "AUTO_DEFAULT",
    "clinic_check_in": "AUTO_DEFAULT",
    # clinic_check_out: clinic_self_study_completed로 통합 (SSOT). 발송 코드 제거됨.
    "clinic_absent": "AUTO_DEFAULT",
    "clinic_reminder": "AUTO_DEFAULT",
    "clinic_self_study_completed": "AUTO_DEFAULT",
    "clinic_result_notification": "AUTO_DEFAULT",
    "counseling_reservation_created": "AUTO_DEFAULT",

    # MANUAL_DEFAULT — 선생 검토 필요 (preview → confirm)
    "exam_score_published": "MANUAL_DEFAULT",
    "exam_not_taken": "MANUAL_DEFAULT",
    "retake_assigned": "MANUAL_DEFAULT",
    "assignment_not_submitted": "MANUAL_DEFAULT",
    "assignment_registered": "MANUAL_DEFAULT",
    "assignment_due_hours_before": "MANUAL_DEFAULT",
    "withdrawal_complete": "MANUAL_DEFAULT",
    "check_in_complete": "MANUAL_DEFAULT",
    "absent_occurred": "MANUAL_DEFAULT",
    "monthly_report_generated": "MANUAL_DEFAULT",
    "exam_scheduled_days_before": "MANUAL_DEFAULT",
    "exam_start_minutes_before": "MANUAL_DEFAULT",
    "lecture_session_reminder": "MANUAL_DEFAULT",
    "payment_complete": "MANUAL_DEFAULT",
    "payment_due_days_before": "MANUAL_DEFAULT",
    # urgent_notice: 카카오 알림톡 정책 위반으로 제거

    # DISABLED — 현재 정책상 비활성
    "class_enrollment_complete": "DISABLED",
    "enrollment_expiring_soon": "DISABLED",
    "student_signup": "DISABLED",

}


def get_trigger_policy(trigger: str) -> str:
    """트리거의 정책 분류 반환. 미등록 트리거는 DISABLED."""
    return TRIGGER_POLICY.get(trigger, "DISABLED")


# ──────────────────────────────────────────
# 테넌트별 메시징 제한 — 계정 관련만 허용
# ──────────────────────────────────────────
# 제한된 테넌트: 가입/등록/비번 관련 알림톡만 발송 가능.
# 이 트리거들은 send_alimtalk_via_owner / send_welcome_messages /
# send_registration_approved_messages에서 OWNER_TENANT_ID로 발송되므로
# enqueue_sms 단에서 tenant_id 기준 차단 시 자동 우회됨.
RESTRICTED_MESSAGING_TENANTS: frozenset = frozenset()  # 림글리쉬 제한 해제 (뿌리오 자체 연동 완료)


def is_messaging_restricted(tenant_id: int) -> bool:
    """해당 테넌트의 비계정(non-account) 메시징이 제한되어 있는지 여부."""
    return int(tenant_id) in RESTRICTED_MESSAGING_TENANTS


def get_owner_tenant_id() -> int:
    """SMS 발송이 허용된 tenant ID (내 테넌트)."""
    return getattr(settings, "OWNER_TENANT_ID", 1)


def get_test_tenant_id() -> int:
    """로컬 기능 테스트용 tenant ID. 이 tenant에서는 알림톡·문자 발송 없이 기능만 동작."""
    return getattr(settings, "TEST_TENANT_ID", 9999)


def is_messaging_disabled(tenant_id: int) -> bool:
    """해당 tenant가 메시징(알림톡·문자) 비활성화(테스트용)인지. True면 발송하지 않고 스킵."""
    return int(tenant_id) == get_test_tenant_id()


def get_messaging_test_whitelist() -> frozenset[str]:
    """
    메시징 테스트 모드 수신자 허용 번호.
    MESSAGING_TEST_WHITELIST 환경변수에 콤마 구분 번호가 있으면 해당 번호만 실발송 허용.
    비어 있거나 미설정이면 테스트 모드 비활성 (모든 번호 허용).
    """
    import os
    raw = os.environ.get("MESSAGING_TEST_WHITELIST", "").strip()
    if not raw:
        return frozenset()
    return frozenset(n.replace("-", "").strip() for n in raw.split(",") if n.strip())


def check_recipient_allowed(to: str) -> bool:
    """
    수신 번호가 발송 허용 대상인지 확인.
    테스트 모드(MESSAGING_TEST_WHITELIST 설정됨)에서는 whitelist에 있는 번호만 허용.
    운영 모드(미설정)에서는 모든 번호 허용.

    Returns:
        True if allowed, False if blocked
    """
    whitelist = get_messaging_test_whitelist()
    if not whitelist:
        return True  # 운영 모드: 모든 번호 허용
    normalized = (to or "").replace("-", "").strip()
    if normalized in whitelist:
        return True
    logger.warning(
        "recipient_guard: blocked sending to %s (not in whitelist: %s)",
        normalized[:4] + "****", ",".join(sorted(whitelist)),
    )
    return False


def is_event_dry_run(trigger: str) -> bool:
    """
    특정 이벤트 트리거가 dry-run 모드인지 확인.
    dry-run이면 로그만 남기고 실제 발송하지 않음.

    환경변수 MESSAGING_DRY_RUN_TRIGGERS:
    - "*" : 모든 이벤트 트리거 dry-run (가입/비번 제외)
    - "check_in_complete,absent_occurred" : 특정 트리거만 dry-run
    - 비어있으면 dry-run 없음 (운영 모드)

    가입 안내/비밀번호 관련 트리거는 dry-run 대상에서 제외:
    registration_approved_*, password_find_otp, password_reset_*
    """
    import os
    raw = os.environ.get("MESSAGING_DRY_RUN_TRIGGERS", "").strip()
    if not raw:
        return False

    # 가입/비밀번호 관련은 항상 실발송 (dry-run 제외)
    ALWAYS_LIVE_TRIGGERS = frozenset([
        "registration_approved_student",
        "registration_approved_parent",
        "password_find_otp",
        "password_reset_student",
        "password_reset_parent",
    ])
    if trigger in ALWAYS_LIVE_TRIGGERS:
        return False

    if raw == "*":
        return True
    dry_triggers = frozenset(t.strip() for t in raw.split(",") if t.strip())
    return trigger in dry_triggers


def _has_own_sms_credentials(tenant_id: int) -> bool:
    """테넌트가 자체 SMS 발송 가능한 연동 키를 갖고 있는지."""
    try:
        creds = get_tenant_own_credentials(tenant_id)
        provider = creds.get("provider", "solapi")
        if provider == "ppurio":
            return bool(creds.get("ppurio_api_key") and creds.get("ppurio_account"))
        return bool(creds.get("solapi_api_key") and creds.get("solapi_api_secret"))
    except Exception:
        return False


def can_send_sms(tenant_id: int) -> bool:
    """해당 tenant가 문자(SMS/LMS) 발송을 허용하는지 여부.
    - 시스템 키 사용: OWNER_TENANT_ID만 허용 (플랫폼 SMS 비용 보호)
    - 자체 연동 키 보유: 해당 테넌트 자체 계정 사용이므로 허용
    """
    if is_messaging_disabled(tenant_id):
        return False
    if int(tenant_id) == get_owner_tenant_id():
        return True
    return _has_own_sms_credentials(tenant_id)


class MessagingPolicyError(Exception):
    """메시징 정책 위반 (예: 비허용 tenant의 SMS 요청)."""
    def __init__(self, message: str, reason: str = "policy"):
        super().__init__(message)
        self.reason = reason


def resolve_kakao_channel(tenant_id: int) -> dict:
    """
    알림톡 발송 시 사용할 카카오 채널(PF ID) 결정.

    우선순위: (1) tenant 연동 채널(kakao_pfid) (2) 시스템 기본 채널(settings.SOLAPI_KAKAO_PF_ID).
    테스트 tenant(9999)에서는 발송 스킵을 위해 pf_id 빈 문자열 반환.
    """
    if is_messaging_disabled(tenant_id):
        return {"pf_id": "", "use_default": True}
    pf_id_tenant = ""
    if tenant_id is not None:
        try:
            from apps.support.messaging.credit_services import get_tenant_messaging_info
            info = get_tenant_messaging_info(int(tenant_id))
            if info:
                pf_id_tenant = (info.get("kakao_pfid") or "").strip()
        except Exception as e:
            logger.warning("resolve_kakao_channel get_tenant_messaging_info failed: %s", e)
    default_pf_id = (getattr(settings, "SOLAPI_KAKAO_PF_ID", None) or "").strip()
    if pf_id_tenant:
        return {"pf_id": pf_id_tenant, "use_default": False}
    return {"pf_id": default_pf_id or "", "use_default": True}


def get_tenant_provider(tenant_id: int) -> str:
    """
    테넌트의 메시징 공급자(solapi/ppurio) 반환.
    DB 조회 실패 시 기본값 'solapi'.
    """
    try:
        from apps.core.models import Tenant
        provider = (
            Tenant.objects.filter(pk=int(tenant_id))
            .values_list("messaging_provider", flat=True)
            .first()
        )
        return (provider or "solapi").strip().lower()
    except Exception as e:
        logger.warning("get_tenant_provider failed: %s", e)
        return "solapi"


def get_tenant_own_credentials(tenant_id: int) -> dict:
    """
    테넌트 자체 연동 키 반환. 직접 연동 모드에서 사용.
    Returns: {"solapi_api_key", "solapi_api_secret", "ppurio_api_key", "ppurio_account", "provider"}
    비어 있으면 시스템 기본 키 사용.
    """
    try:
        from apps.core.models import Tenant
        t = Tenant.objects.filter(pk=int(tenant_id)).values(
            "messaging_provider",
            "own_solapi_api_key", "own_solapi_api_secret",
            "own_ppurio_api_key", "own_ppurio_account",
        ).first()
        if not t:
            return {}
        provider = (t.get("messaging_provider") or "solapi").strip().lower()
        return {
            "provider": provider,
            "solapi_api_key": (t.get("own_solapi_api_key") or "").strip(),
            "solapi_api_secret": (t.get("own_solapi_api_secret") or "").strip(),
            "ppurio_api_key": (t.get("own_ppurio_api_key") or "").strip(),
            "ppurio_account": (t.get("own_ppurio_account") or "").strip(),
        }
    except Exception as e:
        logger.warning("get_tenant_own_credentials failed: %s", e)
        return {}


def resolve_messaging_provider(tenant_id: int, message_type: str) -> dict:
    """
    발송 유형별 허용 여부 및 채널 정보를 한 곳에서 결정.

    Args:
        tenant_id: 테넌트 ID
        message_type: "sms" | "alimtalk"

    Returns:
        - message_type == "sms":
          {"allowed": bool, "reason": str | None, "provider": str}
        - message_type == "alimtalk":
          {"allowed": True, "pf_id": str, "use_default": bool, "provider": str}
    """
    tenant_id = int(tenant_id)
    provider = get_tenant_provider(tenant_id)
    if message_type == "sms":
        allowed = can_send_sms(tenant_id)
        return {
            "allowed": allowed,
            "reason": None if allowed else "sms_not_allowed",
            "provider": provider,
        }
    if message_type == "alimtalk":
        channel = resolve_kakao_channel(tenant_id)
        return {
            "allowed": True,
            "pf_id": channel["pf_id"],
            "use_default": channel["use_default"],
            "provider": provider,
        }
    return {"allowed": False, "reason": "unknown_message_type", "provider": provider}


def send_alimtalk_via_owner(trigger: str, to: str, replacements: dict[str, str]) -> bool:
    """
    오너 테넌트의 승인된 알림톡 템플릿으로 발송.
    모든 테넌트에서 학생 인증 관련 알림톡은 이 함수를 사용.
    SMS fallback 없음 — 알림톡 온리.

    Args:
        trigger: AutoSendConfig trigger (예: "password_find_otp")
        to: 수신자 전화번호 (01012345678)
        replacements: 템플릿 치환 맵 (예: {"인증번호": "123456"})

    Returns:
        True if enqueue 성공
    """
    from apps.support.messaging.selectors import get_auto_send_config
    from apps.support.messaging.services import enqueue_sms

    owner_id = get_owner_tenant_id()

    if is_messaging_disabled(owner_id):
        logger.info("send_alimtalk_via_owner: messaging disabled for owner tenant")
        return True  # 테스트 환경에서는 성공 간주

    config = get_auto_send_config(owner_id, trigger)
    t = config.template if config else None
    solapi_id = (t.solapi_template_id or "").strip() if t else ""

    # 승인된 템플릿이 없으면 fallback 트리거 시도
    # 비번 리셋 → 가입 승인 템플릿 재활용 (같은 플레이스홀더)
    FALLBACK_TRIGGERS = {
        "password_reset_student": "registration_approved_student",
        "password_reset_parent": "registration_approved_parent",
        "password_find_otp": "registration_approved_student",
    }
    if not solapi_id or (t and t.solapi_status != "APPROVED"):
        fallback_trigger = FALLBACK_TRIGGERS.get(trigger)
        if fallback_trigger:
            fb_config = get_auto_send_config(owner_id, fallback_trigger)
            if fb_config and fb_config.template:
                fb_t = fb_config.template
                fb_id = (fb_t.solapi_template_id or "").strip()
                if fb_id and fb_t.solapi_status == "APPROVED":
                    logger.info(
                        "send_alimtalk_via_owner: fallback %s → %s (template=%s)",
                        trigger, fallback_trigger, fb_id,
                    )
                    t = fb_t
                    solapi_id = fb_id

    if not t or not solapi_id or t.solapi_status != "APPROVED":
        logger.error(
            "send_alimtalk_via_owner: no approved template trigger=%s owner=%s",
            trigger, owner_id,
        )
        return False

    # 본문 치환 — text와 variables 모두 정확히 일치해야 카카오 검증 통과
    text = (t.body or "").strip()
    alimtalk_replacements = []
    for key, value in replacements.items():
        placeholder = f"#{{{key}}}"
        text = text.replace(placeholder, str(value))
        alimtalk_replacements.append({"key": key, "value": str(value)})

    try:
        return enqueue_sms(
            tenant_id=owner_id,
            to=to,
            text=text,
            message_mode="alimtalk",
            template_id=solapi_id,
            alimtalk_replacements=alimtalk_replacements,
        )
    except Exception as exc:
        logger.error("send_alimtalk_via_owner: enqueue failed trigger=%s error=%s", trigger, exc)
        return False
