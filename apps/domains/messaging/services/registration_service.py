# apps/support/messaging/services/registration_service.py
"""
가입/승인 알림 발송 — send_welcome_messages, send_registration_approved_messages
"""

import logging

logger = logging.getLogger(__name__)


# 가입 승인 알림톡용 플레이스홀더
REGISTRATION_APPROVED_NOTICE = "접속해서 ID·비밀번호를 변경할 수 있습니다."


def send_welcome_messages(
    *,
    created_students: list,
    student_password: str,
    parent_password_by_phone: dict = None,
    site_url: str = "",
):
    """
    가입 안내 알림톡 일괄 발송 (학생 + 학부모).

    셀프가입 승인과 동일한 솔라피 승인 템플릿 사용:
    - 학생: registration_approved_student (#{학생이름}, #{학생아이디}, #{학생비밀번호}, #{사이트링크}, #{비밀번호안내})
    - 학부모: registration_approved_parent (위 + #{학부모아이디}, #{학부모비밀번호})
    """
    from .queue_service import enqueue_sms
    from .url_helpers import get_tenant_site_url

    parent_password_by_phone = parent_password_by_phone or {}
    sent = 0

    if not created_students:
        return {"status": "skip", "enqueued": 0}

    from apps.domains.messaging.policy import MessagingPolicyError, get_owner_tenant_id

    tenant_id = getattr(created_students[0], "tenant_id", None)

    # site_url이 비어 있으면 테넌트의 primary domain에서 자동 파생
    if not site_url:
        tenant_obj = getattr(created_students[0], "tenant", None)
        if tenant_obj is None and tenant_id:
            try:
                from apps.core.models import Tenant
                tenant_obj = Tenant.objects.get(pk=tenant_id)
            except Exception:
                tenant_obj = None
        site_url = get_tenant_site_url(tenant_obj)
    if not tenant_id:
        logger.warning("send_welcome: no tenant_id, skip")
        return {"status": "skip", "enqueued": 0}

    owner_id = get_owner_tenant_id()
    notice = REGISTRATION_APPROVED_NOTICE

    # 셀프가입 승인과 동일한 템플릿 resolve (학생용, 학부모용 각각)
    def _resolve(trigger: str):
        from apps.domains.messaging.selectors import get_auto_send_config
        from apps.domains.messaging.models import MessageTemplate
        config = get_auto_send_config(owner_id, trigger)
        if config and config.enabled and config.template:
            t = config.template
            sid = (t.solapi_template_id or "").strip()
            if sid and t.solapi_status == "APPROVED":
                return t, sid
        t = MessageTemplate.objects.filter(
            tenant_id=owner_id, category="signup", solapi_status="APPROVED",
        ).exclude(solapi_template_id="").order_by("pk").first()
        if t:
            return t, (t.solapi_template_id or "").strip()
        return None, None

    tmpl_student, sid_student = _resolve("registration_approved_student")
    tmpl_parent, sid_parent = _resolve("registration_approved_parent")

    if not tmpl_student and not tmpl_parent:
        for student in created_students:
            logger.info("send_welcome (stub) student=%s", getattr(student, "name", ""))
        return {"status": "stub", "logged": len(created_students)}

    for student in created_students:
        name = (getattr(student, "name", "") or "").strip()
        ps_number = (getattr(student, "ps_number", "") or "").strip()
        phone = (getattr(student, "phone", "") or "").replace("-", "").strip()
        parent_phone = (getattr(student, "parent_phone", "") or "").replace("-", "").strip()

        # 학생용 — registration_approved_student 템플릿
        if phone and len(phone) >= 10 and tmpl_student and sid_student:
            replacements = {
                "학생이름": name,
                "학생아이디": ps_number,
                "학생비밀번호": student_password,
                "사이트링크": site_url,
                "비밀번호안내": notice,
            }
            body = (tmpl_student.body or "").strip()
            text = body
            for k, v in replacements.items():
                text = text.replace(f"#{{{k}}}", v)
            try:
                ok = enqueue_sms(
                    tenant_id=owner_id,
                    to=phone,
                    text=text,
                    message_mode="alimtalk",
                    template_id=sid_student,
                    alimtalk_replacements=[{"key": k, "value": v} for k, v in replacements.items()],
                )
            except MessagingPolicyError:
                logger.info("send_welcome student skipped (policy: tenant_id=%s)", tenant_id)
                ok = False
            if ok:
                sent += 1

        # 학부모용 — registration_approved_parent 템플릿
        if parent_phone and len(parent_phone) >= 10 and tmpl_parent and sid_parent:
            pwd = parent_password_by_phone.get(parent_phone)
            if not pwd:
                logger.error(
                    "send_welcome_messages SKIP parent: phone=%s no password in mapping, refusing to send with empty/default password",
                    parent_phone[:4] + "****",
                )
                continue
            replacements = {
                "학생이름": name,
                "학생아이디": ps_number,
                "학생비밀번호": student_password,
                "학부모아이디": parent_phone,
                "학부모비밀번호": pwd,
                "사이트링크": site_url,
                "비밀번호안내": notice,
            }
            body = (tmpl_parent.body or "").strip()
            text = body
            for k, v in replacements.items():
                text = text.replace(f"#{{{k}}}", v)
            try:
                ok = enqueue_sms(
                    tenant_id=owner_id,
                    to=parent_phone,
                    text=text,
                    message_mode="alimtalk",
                    template_id=sid_parent,
                    alimtalk_replacements=[{"key": k, "value": v} for k, v in replacements.items()],
                )
            except MessagingPolicyError:
                logger.info("send_welcome parent skipped (policy: tenant_id=%s)", tenant_id)
                ok = False
            if ok:
                sent += 1

    return {"status": "enqueued", "enqueued": sent}


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
      플레이스홀더: #{학생이름}, #{학생아이디}, #{학생비밀번호}, #{사이트링크}, #{비밀번호안내}
    - 학부모용: 트리거 registration_approved_parent 템플릿 사용
      플레이스홀더: #{학부모아이디}, #{학부모비밀번호}, #{학생이름}, #{학생아이디}, #{학생비밀번호}, #{사이트링크}, #{비밀번호안내}

    설정이 없거나 비활성화면 발송하지 않음.
    """
    from apps.domains.messaging.selectors import get_auto_send_config
    from apps.domains.messaging.policy import MessagingPolicyError
    from apps.domains.messaging.models import MessageTemplate
    from .queue_service import enqueue_sms

    sent = 0
    student_phone = (student_phone or "").replace("-", "").strip()
    parent_phone = (parent_phone or "").replace("-", "").strip()
    site_url = (site_url or "").strip()
    notice = REGISTRATION_APPROVED_NOTICE

    def _resolve_template(trigger: str):
        """오너 테넌트의 승인된 템플릿 사용 (모든 테넌트 공통). SMS fallback 없음."""
        from apps.domains.messaging.policy import get_owner_tenant_id
        owner_id = get_owner_tenant_id()
        # 1) 오너 테넌트의 AutoSendConfig
        config = get_auto_send_config(owner_id, trigger)
        if config and config.enabled and config.template:
            t = config.template
            solapi_id = (t.solapi_template_id or "").strip()
            if solapi_id and t.solapi_status == "APPROVED":
                return t, solapi_id, "alimtalk"
        # 2) 오너 테넌트의 승인된 signup 카테고리 템플릿 자동 발견
        t = MessageTemplate.objects.filter(
            tenant_id=owner_id,
            category="signup",
            solapi_status="APPROVED",
        ).exclude(solapi_template_id="").order_by("pk").first()
        if t:
            logger.info(
                "send_registration_approved fallback: trigger=%s using owner template=%s (id=%s)",
                trigger, t.name, t.solapi_template_id,
            )
            return t, (t.solapi_template_id or "").strip(), "alimtalk"
        return None, None, "alimtalk"

    replacements_base = {
        "학생이름": student_name or "",
        "학생아이디": student_id or "",
        "학생비밀번호": student_password or "",
        "사이트링크": site_url,
        "비밀번호안내": notice,
    }

    # 학생용
    if student_phone and len(student_phone) >= 10:
        tmpl, solapi_id, mode = _resolve_template("registration_approved_student")
        if tmpl:
            body = (tmpl.body or "").strip()
            text = body
            for k, v in replacements_base.items():
                text = text.replace(f"#{{{k}}}", v)
            # subject를 text에 합치지 않음 — 카카오 알림톡은 body만 검증, subject 합치면 3034 불일치

            alimtalk_replacements = None
            template_id_solapi = None
            if solapi_id:
                template_id_solapi = solapi_id
                alimtalk_replacements = [{"key": k, "value": v} for k, v in replacements_base.items()]
            try:
                from apps.domains.messaging.policy import get_owner_tenant_id as _owner
                if enqueue_sms(
                    tenant_id=_owner(),
                    to=student_phone,
                    text=text,
                    message_mode="alimtalk",
                    template_id=template_id_solapi,
                    alimtalk_replacements=alimtalk_replacements,
                ):
                    sent += 1
            except MessagingPolicyError:
                logger.info("send_registration_approved student skipped (policy: tenant_id=%s)", tenant_id)
        else:
            logger.warning(
                "send_registration_approved student: no template found (tenant_id=%s, trigger=registration_approved_student)",
                tenant_id,
            )

    # 학부모용
    if parent_phone and len(parent_phone) >= 10:
        tmpl, solapi_id, mode = _resolve_template("registration_approved_parent")
        if tmpl:
            parent_id_display = parent_phone
            parent_replacements = {
                **replacements_base,
                "학부모아이디": parent_id_display,
                "학부모비밀번호": parent_password or "",
            }
            body = (tmpl.body or "").strip()
            text = body
            for k, v in parent_replacements.items():
                text = text.replace(f"#{{{k}}}", v)
            # subject를 text에 합치지 않음 — 카카오 알림톡은 body만 검증

            alimtalk_replacements = None
            template_id_solapi = None
            if solapi_id:
                template_id_solapi = solapi_id
                alimtalk_replacements = [{"key": k, "value": v} for k, v in parent_replacements.items()]
            try:
                from apps.domains.messaging.policy import get_owner_tenant_id as _owner
                if enqueue_sms(
                    tenant_id=_owner(),
                    to=parent_phone,
                    text=text,
                    message_mode="alimtalk",
                    template_id=template_id_solapi,
                    alimtalk_replacements=alimtalk_replacements,
                ):
                    sent += 1
            except MessagingPolicyError:
                logger.info("send_registration_approved parent skipped (policy: tenant_id=%s)", tenant_id)
        else:
            logger.warning(
                "send_registration_approved parent: no template found (tenant_id=%s, trigger=registration_approved_parent)",
                tenant_id,
            )

    if sent:
        return {"status": "enqueued", "enqueued": sent}
    return {"status": "skip", "enqueued": 0}
