# SSOT 문서: backend/.claude/domains/messaging.md (수정 시 문서도 동기화)
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
from libs.redis.idempotency import acquire_job_lock, release_job_lock, RedisLockUnavailableError

from apps.worker.messaging_worker.config import load_config

# 트리거명 → 사용자 친화적 라벨 (발송 내역 표시용)
_TRIGGER_LABELS: dict[str, str] = {
    "check_in_complete": "입실 완료",
    "absent_occurred": "결석 발생",
    "lecture_session_reminder": "수업 시작 알림",
    "exam_score_published": "성적 공개",
    "exam_not_taken": "시험 미응시",
    "retake_assigned": "재시험 대상",
    "exam_scheduled_days_before": "시험 예정 안내",
    "exam_start_minutes_before": "시험 시작 알림",
    "assignment_not_submitted": "과제 미제출",
    "assignment_registered": "과제 등록",
    "assignment_due_hours_before": "과제 마감 알림",
    "monthly_report_generated": "월간 리포트",
    "withdrawal_complete": "퇴원 처리",
    "payment_complete": "결제 완료",
    "payment_due_days_before": "납부 예정 안내",
    "clinic_reminder": "클리닉 시작 알림",
    "clinic_reservation_created": "클리닉 예약",
    "clinic_reservation_changed": "클리닉 변경",
    "clinic_cancelled": "클리닉 취소",
    "clinic_check_in": "클리닉 입실",
    "clinic_absent": "클리닉 결석",
    "clinic_self_study_completed": "클리닉 완료",
    "clinic_result_notification": "클리닉 결과",
    "counseling_reservation_created": "상담 예약",
    "video_encoding_complete": "영상 인코딩 완료",
    "registration_approved_student": "가입 안내(학생)",
    "registration_approved_parent": "가입 안내(학부모)",
    "password_find_otp": "비밀번호 찾기",
    "password_reset_student": "비밀번호 재설정(학생)",
    "password_reset_parent": "비밀번호 재설정(학부모)",
}


def _get_template_summary(event_type: str, template_id: str, message_mode: str) -> str:
    """발송 내역에 표시할 사람이 읽을 수 있는 요약."""
    # manual_ 접두어 제거 (수동 발송)
    trigger = event_type.removeprefix("manual_") if event_type else ""
    label = _TRIGGER_LABELS.get(trigger, "")
    if label:
        mode = "알림톡" if message_mode == "alimtalk" else "SMS"
        return f"{label} ({mode})"
    if message_mode == "alimtalk" and template_id:
        return f"알림톡"
    return "SMS" if message_mode == "sms" else message_mode or "SMS"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [MESSAGING-WORKER] %(message)s",
)
logger = logging.getLogger("messaging_worker")

# 메시지 발송 구간별 진행률 (n/4): 업로드 마법사처럼 단계별 0~100% 제공
MESSAGING_STEP_TOTAL = 4
MESSAGING_STEPS = [
    (1, "checking", "예약확인"),
    (2, "validating", "잔액확인"),
    (3, "sending", "발송"),
    (4, "done", "완료"),
]
_STEP_DISPLAY = {step_name: display for _, step_name, display in MESSAGING_STEPS}


def _record_progress(
    job_id: str,
    step: str,
    percent: int,
    step_index: int | None = None,
    step_percent: int | None = None,
    tenant_id: str | None = None,  # ✅ 추가: tenant_id 전달
) -> None:
    """Redis 진행률 기록 (우하단 실시간 프로그래스바용). 구간별 진행률 지원."""
    try:
        from academy.adapters.cache.redis_progress_adapter import RedisProgressAdapter
        extra = {"percent": percent}
        if step_index is not None:
            extra.update({
                "step_index": step_index,
                "step_total": MESSAGING_STEP_TOTAL,
                "step_name": step,
                "step_name_display": _STEP_DISPLAY.get(step, step),
                "step_percent": step_percent if step_percent is not None else 100,
            })
        # ✅ tenant_id 전달 (tenant namespace 키 사용)
        tenant_id_str = str(tenant_id) if tenant_id else None
        RedisProgressAdapter().record_progress(job_id, step, extra, tenant_id=tenant_id_str)
    except Exception as e:
        logger.debug("Redis progress record skip: %s", e)


_shutdown = False
_current_receipt_handle: Optional[str] = None


def _handle_signal(sig, frame):
    """SIGTERM/SIGINT: graceful shutdown. In-flight 메시지는 delete하지 않음 → visibility 만료 후 재노출되어 안전 재처리."""
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


def send_one_alimtalk_ppurio(
    to: str,
    sender: str,
    pf_id: str,
    template_id: str,
    replacements: Optional[list] = None,
    *,
    api_key: str = "",
    account: str = "",
) -> dict:
    """뿌리오 알림톡 1건 발송. Solapi send_one_alimtalk과 동일 인터페이스."""
    try:
        from apps.support.messaging.ppurio_client import send_ppurio_alimtalk
        return send_ppurio_alimtalk(
            to=to, sender=sender, pf_id=pf_id,
            template_id=template_id, replacements=replacements,
            api_key=api_key, account=account,
        )
    except Exception as e:
        logger.exception("ppurio alimtalk failed to=%s****", (to or "")[:4])
        return {"status": "error", "reason": str(e)[:500]}


def send_one_sms_ppurio(
    to: str, text: str, sender: str,
    *, api_key: str = "", account: str = "",
) -> dict:
    """뿌리오 SMS/LMS 1건 발송. Solapi send_one_sms와 동일 인터페이스."""
    try:
        from apps.support.messaging.ppurio_client import send_ppurio_sms
        return send_ppurio_sms(to=to, text=text, sender=sender, api_key=api_key, account=account)
    except Exception as e:
        logger.exception("ppurio sms failed to=%s****", (to or "")[:4])
        return {"status": "error", "reason": str(e)[:500]}


def send_one_sms_own_solapi(
    to: str, text: str, sender: str,
    *, api_key: str, api_secret: str,
) -> dict:
    """테넌트 자체 솔라피 키로 SMS 1건 발송."""
    try:
        from solapi.model import RequestMessage
        from solapi.model.message_type import MessageType
        from solapi import SolapiMessageService
    except ImportError as e:
        return {"status": "error", "reason": "solapi_not_installed"}
    client = SolapiMessageService(api_key=api_key, api_secret=api_secret)
    sender = (sender or "").strip().replace("-", "")
    to = (to or "").replace("-", "").strip()
    text = (text or "").strip()
    if not to or not text or not sender:
        return {"status": "error", "reason": "to_text_sender_required"}
    text_bytes = text.encode("utf-8")
    if len(text_bytes) <= 90:
        message = RequestMessage(from_=sender, to=to, text=text, type=MessageType.SMS)
    else:
        subject = (text[:20] + "…") if len(text) > 20 else text
        message = RequestMessage(from_=sender, to=to, text=text, type=MessageType.LMS, subject=subject)
    try:
        response = client.send(message)
        group_id = getattr(getattr(response, "group_info", None), "group_id", None)
        logger.info("send_sms_own ok to=%s**** group_id=%s", to[:4], group_id)
        return {"status": "ok", "group_id": group_id}
    except Exception as e:
        logger.warning("send_sms_own failed to=%s****: %s", to[:4], e)
        return {"status": "error", "reason": str(e)[:500]}


def _build_variables_dict(replacements: Optional[list]) -> Optional[dict]:
    """Convert [{"key": "학생이름2", "value": "길동"}, ...] → {"#{학생이름2}": "길동", ...}"""
    if not replacements:
        return None
    variables = {}
    for r in replacements:
        if isinstance(r, dict) and "key" in r and "value" in r:
            key = str(r["key"])
            val = str(r["value"])
            if not key.startswith("#{"):
                key = f"#{{{key}}}"
            variables[key] = val
    return variables if variables else None


def send_one_alimtalk_own_solapi(
    to: str, sender: str, pf_id: str, template_id: str,
    replacements: Optional[list] = None,
    *, api_key: str, api_secret: str, text: str = "",
) -> dict:
    """테넌트 자체 솔라피 키로 알림톡 1건 발송."""
    try:
        from solapi.model import RequestMessage
        from solapi.model.kakao.kakao_option import KakaoOption
        from solapi import SolapiMessageService
    except ImportError:
        return {"status": "error", "reason": "solapi_not_installed"}
    client = SolapiMessageService(api_key=api_key, api_secret=api_secret)
    to = (to or "").replace("-", "").strip()
    if not to or not pf_id or not template_id:
        return {"status": "error", "reason": "to_pf_template_required"}
    try:
        variables = _build_variables_dict(replacements)
        kakao_option = KakaoOption(pf_id=pf_id, template_id=template_id, variables=variables, disable_sms=True)
        message = RequestMessage(from_=sender, to=to, text=text or " ", kakao_options=kakao_option)
        response = client.send(message)
        group_id = getattr(getattr(response, "group_info", None), "group_id", None)
        count = getattr(getattr(response, "group_info", None), "count", None)
        if count is not None and getattr(count, "registered_success", 0) == 0:
            return {"status": "error", "reason": "alimtalk_failed_or_rejected", "group_id": group_id}
        logger.info("send_alimtalk_own ok to=%s**** group_id=%s", to[:4], group_id)
        return {"status": "ok", "group_id": group_id}
    except Exception as e:
        logger.warning("alimtalk_own failed to=%s****: %s", to[:4], e)
        return {"status": "error", "reason": str(e)[:500]}


def send_one_alimtalk(
    cfg,
    to: str,
    sender: str,
    pf_id: str,
    template_id: str,
    replacements: Optional[list] = None,
    text: str = "",
) -> dict:
    """
    Solapi 알림톡 1건 발송.
    replacements: [{"key": "학생이름2", "value": "길동"}, ...] — 템플릿 #{학생이름2}, #{날짜}, #{클리닉명} 등 치환.
    text: SMS 대체 본문 (Solapi 필수 필드).
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
        variables = _build_variables_dict(replacements)
        kakao_option = KakaoOption(pf_id=pf_id, template_id=template_id, variables=variables, disable_sms=True)
        message = RequestMessage(
            from_=sender,
            to=to,
            text=text or " ",
            kakao_options=kakao_option,
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
    Solapi로 SMS/LMS 1건 발송.
    - 90byte 이하: SMS (type 명시로 자동판단 오류 방지)
    - 90byte 초과: LMS (subject 필수라서 본문 앞 20자 사용)
    Returns: {"status": "ok"|"error", "group_id"?, "reason"?}
    """
    try:
        from solapi.model import RequestMessage
        from solapi.model.message_type import MessageType
    except ImportError as e:
        logger.error("solapi SDK not installed: %s", e)
        return {"status": "error", "reason": "solapi_not_installed"}
    client = _get_solapi_client(cfg)
    sender = (sender or cfg.SOLAPI_SENDER or "").strip().replace("-", "")
    if not sender:
        return {"status": "error", "reason": "sender_required"}

    to = (to or "").replace("-", "").strip()
    text = (text or "").strip()
    if not to or not text:
        return {"status": "error", "reason": "to_and_text_required"}

    # Solapi: SMS 90byte 이하, LMS는 subject 필수. 타입 미지정 시 "발송 가능한 메시지 없음" 발생 가능.
    text_bytes = text.encode("utf-8")
    if len(text_bytes) <= 90:
        message = RequestMessage(from_=sender, to=to, text=text, type=MessageType.SMS)
    else:
        # LMS: subject 필수(장문 제목, 30자 내외 권장)
        subject = (text[:20] + "…") if len(text) > 20 else text
        message = RequestMessage(
            from_=sender, to=to, text=text, type=MessageType.LMS, subject=subject
        )

    try:
        response = client.send(message)
        group_id = getattr(getattr(response, "group_info", None), "group_id", None)
        logger.info("send_sms ok to=%s**** group_id=%s", to[:4], group_id)
        return {"status": "ok", "group_id": group_id}
    except Exception as e:
        reason = str(e)[:500]
        try:
            from solapi.error.MessageNotReceiveError import MessageNotReceivedError
            if isinstance(e, MessageNotReceivedError) and getattr(e, "failed_messages", None):
                parts = []
                for fm in e.failed_messages[:3]:
                    status_code = getattr(fm, "status_code", "") or ""
                    status_message = getattr(fm, "status_message", "") or ""
                    parts.append(f"[{status_code}] {status_message}")
                if parts:
                    reason = "; ".join(parts)[:500]
                logger.warning(
                    "send_sms MessageNotReceivedError to=%s**** reason=%s",
                    to[:4], reason,
                )
            else:
                logger.exception("send_sms failed to=%s****", to[:4])
        except Exception:
            logger.exception("send_sms failed to=%s****", to[:4])
        return {"status": "error", "reason": reason}


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Django context: 예약/유저 등 DB 조회가 필요할 때 ORM 사용 가능하도록
    if os.environ.get("DJANGO_SETTINGS_MODULE"):
        import django
        django.setup()
        logger.info("Django setup done (ORM available)")

        # DB 연결 검증 (startup validation): 연결 불가 시 즉시 종료
        try:
            from django.db import connection as db_conn
            db_conn.ensure_connection()
            logger.info("DB connection validated at startup")
        except Exception as e:
            logger.error("DB connection failed at startup: %s. Exiting.", e)
            return 1

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
                try:
                    lock_acquired = acquire_job_lock(job_id)
                except RedisLockUnavailableError:
                    # Redis 장애: 메시지를 SQS에 남겨 재시도 (fail-closed)
                    logger.warning("Redis unavailable, leaving message in SQS for retry job_id=%s", job_id)
                    time.sleep(10)  # back-off to avoid tight loop during Redis outage
                    continue
                if not lock_acquired:
                    # 중복 메시지: 다른 워커가 이미 처리 중 → 삭제
                    queue_client.delete_message(
                        queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                        receipt_handle=receipt_handle,
                    )
                    continue

                global _current_receipt_handle
                _msg_deleted = False
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
                            _msg_deleted = True
                            continue
                    else:
                        data = body

                    if not isinstance(data, dict) or "to" not in data or "text" not in data:
                        logger.error("Invalid message format: %s", data)
                        queue_client.delete_message(
                            queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                            receipt_handle=receipt_handle,
                        )
                        _msg_deleted = True
                        continue

                    tenant_id = data.get("tenant_id")
                    if tenant_id is None:
                        logger.error(
                            "Message missing tenant_id (required for tenant isolation), "
                            "deleting message_id=%s",
                            message_id,
                        )
                        queue_client.delete_message(
                            queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                            receipt_handle=receipt_handle,
                        )
                        _msg_deleted = True
                        continue

                    # 로컬 기능 테스트용 tenant(9999): 발송·차감 없이 스킵, 메시지 삭제 후 진행
                    if tenant_id is not None and int(tenant_id) == cfg.TEST_TENANT_ID:
                        logger.info(
                            "Message skipped: tenant_id=%s is test tenant (messaging disabled)",
                            tenant_id,
                        )
                        queue_client.delete_message(
                            queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                            receipt_handle=receipt_handle,
                        )
                        _msg_deleted = True
                        continue

                    # 예약 취소 Double Check: 발송 직전 한 번 더 확인
                    reservation_id = data.get("reservation_id")
                    tenant_id_str = str(tenant_id) if tenant_id else None
                    if reservation_id is not None and os.environ.get("DJANGO_SETTINGS_MODULE"):
                        _record_progress(job_id, "checking", 10, step_index=1, step_percent=100, tenant_id=tenant_id_str)
                        try:
                            from apps.support.messaging.services import is_reservation_cancelled
                            if is_reservation_cancelled(int(reservation_id), tenant_id=tenant_id):
                                logger.info("reservation_id=%s cancelled, skip send", reservation_id)
                                queue_client.delete_message(
                                    queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                                    receipt_handle=receipt_handle,
                                )
                                _msg_deleted = True
                                _current_receipt_handle = None
                                continue
                        except Exception as e:
                            logger.warning("reservation check failed: %s", e)
                    else:
                        _record_progress(job_id, "checking", 10, step_index=1, step_percent=100, tenant_id=tenant_id_str)

                    _current_receipt_handle = receipt_handle

                    to = str(data.get("to", "")).replace("-", "").strip()
                    text = str(data.get("text", ""))
                    sender = (data.get("sender") or "").strip()
                    target_name = (data.get("target_name") or "").strip()
                    message_mode = (data.get("message_mode") or "").strip().lower()
                    if not message_mode or message_mode not in ("sms", "alimtalk"):
                        message_mode = "sms"
                    alimtalk_replacements = data.get("alimtalk_replacements") or []
                    template_id_msg = data.get("template_id") or ""
                    event_type_msg = (data.get("event_type") or "").strip()[:30]

                    # 테넌트별 잔액·PFID·발신번호·단가·공급자 (Django 있을 때만)
                    info = None
                    base_price = "0"
                    pf_id_tenant = ""
                    tenant_provider = "solapi"  # 기본 공급자
                    own_creds = {}  # 테넌트 자체 연동 키
                    use_default_channel = True  # 시스템 기본 알림톡 채널 사용 여부
                    if tenant_id is not None and os.environ.get("DJANGO_SETTINGS_MODULE"):
                        try:
                            from apps.support.messaging.credit_services import (
                                get_tenant_messaging_info,
                                deduct_credits,
                                rollback_credits,
                            )
                            from apps.support.messaging.models import NotificationLog
                            from apps.support.messaging.policy import resolve_kakao_channel, get_tenant_provider, get_tenant_own_credentials
                            from apps.core.models import Tenant
                            info = get_tenant_messaging_info(int(tenant_id))
                            if info:
                                base_price = info["base_price"]
                                pf_id_tenant = (info["kakao_pfid"] or "").strip()
                                if not sender and info.get("sender"):
                                    sender = (info["sender"] or "").strip()
                            # 알림톡 채널: resolver로 통일 (tenant 연동 채널 → 시스템 기본)
                            channel = resolve_kakao_channel(int(tenant_id))
                            pf_id_tenant = (channel.get("pf_id") or "").strip()
                            use_default_channel = channel.get("use_default", True)
                            # 공급자 결정
                            tenant_provider = get_tenant_provider(int(tenant_id))
                            # 테넌트 자체 연동 키 (직접 연동 모드)
                            own_creds = get_tenant_own_credentials(int(tenant_id))
                        except Exception as e:
                            logger.warning("get_tenant_messaging_info/resolve_kakao_channel failed: %s", e)

                    # 발신번호: payload > 테넌트 > 전역 env
                    sender = (sender or "").strip() or cfg.SOLAPI_SENDER

                    # 알림톡 사용 시: resolver 결과 또는 워커 기본 PFID
                    pf_id = pf_id_tenant or cfg.SOLAPI_KAKAO_PF_ID
                    template_id = (template_id_msg or "").strip() or cfg.SOLAPI_KAKAO_TEMPLATE_ID

                    # Business-level atomic claim (Layer 2: DB dedup via unique constraint)
                    business_key = data.get("business_idempotency_key", "")
                    claim_log_id = None
                    if business_key and tenant_id is not None and os.environ.get("DJANGO_SETTINGS_MODULE"):
                        try:
                            from academy.adapters.db.django.repositories_messaging import claim_notification_slot
                            claimed, claim_log_id = claim_notification_slot(
                                tenant_id=int(tenant_id),
                                message_mode=message_mode or "sms",
                                business_idempotency_key=business_key,
                                sqs_message_id=message_id,
                                recipient_summary=(f"{target_name} " if target_name else "") + (to[:4] + "****" if to else ""),
                            )
                            if not claimed:
                                logger.info(
                                    "Business dedup: key=%s already claimed, skipping (tenant=%s)",
                                    business_key[:16], tenant_id,
                                )
                                queue_client.delete_message(
                                    queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                                    receipt_handle=receipt_handle,
                                )
                                _msg_deleted = True
                                _current_receipt_handle = None
                                continue
                        except Exception as e:
                            logger.warning("Business claim failed (proceeding with legacy path): %s", e)
                            claim_log_id = None
                    else:
                        # Legacy DB-level dedup: Redis 장애 복구 후 재처리 시 이미 발송된 메시지 스킵
                        if tenant_id is not None and os.environ.get("DJANGO_SETTINGS_MODULE"):
                            try:
                                from apps.support.messaging.models import NotificationLog as _NL
                                if _NL.objects.filter(
                                    sqs_message_id=message_id, success=True
                                ).exists():
                                    logger.info(
                                        "DB dedup: message_id=%s already sent successfully, skipping",
                                        message_id,
                                    )
                                    queue_client.delete_message(
                                        queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                                        receipt_handle=receipt_handle,
                                    )
                                    _msg_deleted = True
                                    _current_receipt_handle = None
                                    continue
                            except Exception as e:
                                logger.warning("DB dedup check failed (proceeding): %s", e)

                    # 잔액 검증 및 차감 (Django + info 있을 때, 단가 > 0)
                    deducted = False
                    try:
                        if info and float(base_price) > 0 and tenant_id is not None:
                            _record_progress(job_id, "validating", 30, step_index=2, step_percent=0, tenant_id=tenant_id_str)
                            from decimal import Decimal
                            from apps.support.messaging.credit_services import deduct_credits
                            from academy.adapters.db.django.repositories_messaging import create_notification_log
                            bal = info.get("credit_balance", "0")
                            if float(bal) < float(base_price):
                                _record_progress(job_id, "validating", 30, step_index=2, step_percent=100, tenant_id=tenant_id_str)
                                logger.warning(
                                    "tenant_id=%s insufficient_balance balance=%s base_price=%s, skip send",
                                    tenant_id, bal, base_price,
                                )
                                create_notification_log(
                                    tenant_id=int(tenant_id),
                                    success=False,
                                    amount_deducted=Decimal("0"),
                                    recipient_summary=(f"{target_name} " if target_name else "") + (to[:4] + "****"),
                                    failure_reason="insufficient_balance",
                                    message_body=text[:2000],
                                    message_mode=message_mode,
                                    sqs_message_id=message_id,
                                    notification_type=event_type_msg,
                                )
                                queue_client.delete_message(
                                    queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                                    receipt_handle=receipt_handle,
                                )
                                _msg_deleted = True
                                _current_receipt_handle = None
                                continue
                            deduct_credits(int(tenant_id), base_price)
                            deducted = True
                            _record_progress(job_id, "validating", 50, step_index=2, step_percent=100, tenant_id=tenant_id_str)
                        else:
                            _record_progress(job_id, "validating", 50, step_index=2, step_percent=100, tenant_id=tenant_id_str)
                    except Exception as e:
                        logger.exception("deduct_credits failed: %s", e)
                        _current_receipt_handle = None
                        _msg_deleted = True  # 발송 전 실패 → 중복 위험 없음, 잠금 해제하여 재시도 허용
                        consecutive_errors += 1
                        continue

                    # 테넌트 자체 연동 키가 있으면 사용, 없으면 시스템 기본
                    _own = own_creds
                    _own_ppurio_key = (_own.get("ppurio_api_key") or "").strip()
                    _own_ppurio_acct = (_own.get("ppurio_account") or "").strip()
                    _own_solapi_key = (_own.get("solapi_api_key") or "").strip()
                    _own_solapi_secret = (_own.get("solapi_api_secret") or "").strip()

                    # 공급자별 발송 함수 선택
                    def _dispatch_sms(to_, text_, sender_):
                        if tenant_provider == "ppurio":
                            return send_one_sms_ppurio(
                                to=to_, text=text_, sender=sender_,
                                api_key=_own_ppurio_key, account=_own_ppurio_acct,
                            )
                        if _own_solapi_key and _own_solapi_secret:
                            return send_one_sms_own_solapi(
                                to=to_, text=text_, sender=sender_,
                                api_key=_own_solapi_key, api_secret=_own_solapi_secret,
                            )
                        return send_one_sms(cfg, to=to_, text=text_, sender=sender_)

                    def _dispatch_alimtalk(to_, sender_, pf_id_, template_id_, replacements_, text_=""):
                        # 시스템 기본 채널(Solapi PFID)을 사용하는 경우:
                        # tenant provider와 무관하게 시스템 Solapi로 발송.
                        # 뿌리오는 @xxx 형식 PFID만 지원하므로 Solapi 형식 PFID를 넘기면 실패함.
                        if use_default_channel:
                            logger.info(
                                "alimtalk via system solapi (default channel): tenant=%s provider=%s",
                                tenant_id, tenant_provider,
                            )
                            return send_one_alimtalk(
                                cfg, to=to_, sender=sender_, pf_id=pf_id_,
                                template_id=template_id_, replacements=replacements_, text=text_,
                            )
                        # 테넌트 자체 채널이 있으면 테넌트 공급자로 발송
                        if tenant_provider == "ppurio":
                            return send_one_alimtalk_ppurio(
                                to=to_, sender=sender_, pf_id=pf_id_,
                                template_id=template_id_, replacements=replacements_,
                                api_key=_own_ppurio_key, account=_own_ppurio_acct,
                            )
                        if _own_solapi_key and _own_solapi_secret:
                            return send_one_alimtalk_own_solapi(
                                to=to_, sender=sender_, pf_id=pf_id_,
                                template_id=template_id_, replacements=replacements_,
                                api_key=_own_solapi_key, api_secret=_own_solapi_secret, text=text_,
                            )
                        return send_one_alimtalk(
                            cfg, to=to_, sender=sender_, pf_id=pf_id_,
                            template_id=template_id_, replacements=replacements_, text=text_,
                        )

                    # SMS 정책: 자체 연동 키가 있는 테넌트는 허용, 없으면 OWNER_TENANT_ID만
                    _sms_allowed = (
                        tenant_id is None
                        or int(tenant_id) == cfg.OWNER_TENANT_ID
                        or bool(_own_solapi_key and _own_solapi_secret)
                        or bool(_own_ppurio_key and _own_ppurio_acct)
                    )

                    # message_mode: sms | alimtalk
                    try:
                        _record_progress(job_id, "sending", 70, step_index=3, step_percent=0, tenant_id=tenant_id_str)
                        result = None
                        if message_mode == "sms":
                            # SMS: 자체 키 보유 또는 OWNER_TENANT_ID만 허용
                            if not _sms_allowed:
                                logger.warning(
                                    "SMS blocked by policy: tenant_id=%s (no own credentials, not owner_tenant=%s)",
                                    tenant_id, cfg.OWNER_TENANT_ID,
                                )
                                if deducted:
                                    try:
                                        from apps.support.messaging.credit_services import rollback_credits
                                        rollback_credits(int(tenant_id), base_price)
                                    except Exception as e:
                                        logger.warning("rollback_credits failed: %s", e)
                                if os.environ.get("DJANGO_SETTINGS_MODULE"):
                                    try:
                                        from decimal import Decimal
                                        from academy.adapters.db.django.repositories_messaging import create_notification_log
                                        create_notification_log(
                                            tenant_id=int(tenant_id),
                                            success=False,
                                            amount_deducted=Decimal("0"),
                                            recipient_summary=(f"{target_name} " if target_name else "") + (to[:4] + "****"),
                                            failure_reason="sms_not_allowed_for_tenant",
                                            message_body=text[:2000],
                                            message_mode=message_mode,
                                            sqs_message_id=message_id,
                                            notification_type=event_type_msg,
                                        )
                                    except Exception as e:
                                        logger.warning("create_notification_log failed: %s", e)
                                queue_client.delete_message(
                                    queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                                    receipt_handle=receipt_handle,
                                )
                                _msg_deleted = True
                                _current_receipt_handle = None
                                continue
                            result = _dispatch_sms(to, text, sender)
                        elif message_mode == "alimtalk":
                            if pf_id and template_id:
                                result = _dispatch_alimtalk(
                                    to, sender, pf_id, template_id,
                                    alimtalk_replacements if isinstance(alimtalk_replacements, list) else None,
                                    text_=text,
                                )
                            else:
                                result = {"status": "error", "reason": "alimtalk_requires_pf_id_and_template_id"}
                        else:
                            if not _sms_allowed:
                                logger.warning(
                                    "SMS blocked by policy: tenant_id=%s (no own credentials, not owner)",
                                    tenant_id,
                                )
                                result = {"status": "error", "reason": "sms_not_allowed_for_tenant"}
                            else:
                                result = _dispatch_sms(to, text, sender)
                        _record_progress(job_id, "sending", 90, step_index=3, step_percent=100, tenant_id=tenant_id_str)

                        # 성공 시 로그, 실패 시 롤백 + 로그
                        if tenant_id is not None and os.environ.get("DJANGO_SETTINGS_MODULE") and info:
                            try:
                                from decimal import Decimal
                                from apps.support.messaging.credit_services import rollback_credits
                                from academy.adapters.db.django.repositories_messaging import (
                                    create_notification_log, finalize_notification,
                                )
                                if result.get("status") == "ok":
                                    if claim_log_id is not None:
                                        finalize_notification(
                                            claim_log_id,
                                            success=True,
                                            amount_deducted=Decimal(str(base_price)),
                                            template_summary=_get_template_summary(event_type_msg, template_id, message_mode),
                                            message_body=text[:2000],
                                            notification_type=event_type_msg,
                                        )
                                    else:
                                        create_notification_log(
                                            tenant_id=int(tenant_id),
                                            success=True,
                                            amount_deducted=Decimal(str(base_price)),
                                            recipient_summary=(f"{target_name} " if target_name else "") + (to[:4] + "****"),
                                            template_summary=_get_template_summary(event_type_msg, template_id, message_mode),
                                            message_body=text[:2000],
                                            message_mode=message_mode,
                                            sqs_message_id=message_id,
                                            notification_type=event_type_msg,
                                        )
                                else:
                                    if deducted:
                                        rollback_credits(int(tenant_id), base_price)
                                    raw_reason = result.get("reason", "send_failed")[:500]
                                    # 솔라피 IP 미허용 등으로 실패 시 발송 내역 비고에 안내 문구 추가
                                    if any(
                                        x in (raw_reason or "").lower()
                                        for x in ("forbidden", "허용되지 않은 ip", "unauthorized ip", "unauthorized)")
                                    ):
                                        failure_reason = (
                                            "솔라피 IP 미등록: SOLAPI 콘솔(console.solapi.com)에서 "
                                            "설정 > 허용 IP에 이 서버의 나가는 IP를 추가해 주세요. "
                                        ) + (raw_reason or "")[:400]
                                    else:
                                        failure_reason = raw_reason
                                    if claim_log_id is not None:
                                        finalize_notification(
                                            claim_log_id,
                                            success=False,
                                            failure_reason=failure_reason[:500],
                                            message_body=text[:2000],
                                            notification_type=event_type_msg,
                                        )
                                    else:
                                        create_notification_log(
                                            tenant_id=int(tenant_id),
                                            success=False,
                                            amount_deducted=Decimal("0"),
                                            recipient_summary=(f"{target_name} " if target_name else "") + (to[:4] + "****"),
                                            failure_reason=failure_reason[:500],
                                            message_body=text[:2000],
                                            message_mode=message_mode,
                                            sqs_message_id=message_id,
                                            notification_type=event_type_msg,
                                        )
                            except Exception as e:
                                logger.exception("NotificationLog/rollback failed: %s", e)
                                if deducted and result.get("status") != "ok":
                                    try:
                                        rollback_credits(int(tenant_id), base_price)
                                    except Exception:
                                        pass

                        if result.get("status") == "ok":
                            _record_progress(job_id, "done", 100, step_index=4, step_percent=100, tenant_id=tenant_id_str)
                            queue_client.delete_message(
                                queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                                receipt_handle=receipt_handle,
                            )
                            _msg_deleted = True
                            consecutive_errors = 0
                        else:
                            reason = result.get("reason", "")
                            # 비재시도성 오류: 템플릿 미승인, 변수 불일치 등 → 재시도해도 영구 실패
                            _NON_RETRYABLE = (
                                "alimtalk_failed_or_rejected",
                                "alimtalk_requires_pf_id_and_template_id",
                                "InvalidParameterValue",
                                "TemplateNotApproved",
                                "to_pf_template_required",
                                "to_text_sender_required",
                                "sender_required",
                                "to_and_text_required",
                                "solapi_not_installed",
                                "sms_not_allowed_for_tenant",
                            )
                            is_permanent = any(nr in reason for nr in _NON_RETRYABLE)
                            if is_permanent:
                                logger.error(
                                    "send permanently failed (non-retryable), deleting message: reason=%s to=%s tenant=%s",
                                    reason, to[:4] if to else "?", tenant_id,
                                )
                                queue_client.delete_message(
                                    queue_name=cfg.MESSAGING_SQS_QUEUE_NAME,
                                    receipt_handle=receipt_handle,
                                )
                                _msg_deleted = True
                                consecutive_errors = 0
                            else:
                                logger.warning("send failed, message will retry: %s", reason)
                                consecutive_errors += 1
                                if consecutive_errors >= max_consecutive_errors:
                                    logger.error("Too many consecutive errors (%s), exiting", consecutive_errors)
                                    return 1

                        _current_receipt_handle = None
                    except Exception:
                        if deducted:
                            try:
                                from apps.support.messaging.credit_services import rollback_credits
                                rollback_credits(int(tenant_id), base_price)
                                logger.info("Rolled back credits for tenant_id=%s after send exception", tenant_id)
                            except Exception as rb_err:
                                logger.exception("rollback_credits failed after send exception: %s", rb_err)
                        raise

                    if _shutdown:
                        logger.info("Graceful shutdown: exiting")
                        break
                finally:
                    # 중복 발송 방지: 성공(메시지 삭제)·파싱 오류·검증 실패 등
                    # delete_message가 호출된 경우에만 잠금 해제.
                    # 발송 실패 시 잠금 유지 → SQS VisibilityTimeout(900s)
                    # 내 재전달 시 acquire_job_lock이 차단하여 중복 발송 방지.
                    # 잠금 TTL(1800s) > VisibilityTimeout(900s) 이므로 안전.
                    if _msg_deleted:
                        # 메시지가 이미 삭제됨(성공·파싱오류) → 잠금 해제
                        release_job_lock(job_id)
                    # else: 발송 실패 → 잠금 유지 (TTL 만료까지 중복 차단)

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
