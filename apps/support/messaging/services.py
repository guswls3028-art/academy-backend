# apps/support/messaging/services.py
"""
메시지 발송 서비스
- SMS/카카오 등 실제 발송 연동 시 여기서 구현
- 현재는 스텁 (로깅만)
"""

import logging

logger = logging.getLogger(__name__)


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
