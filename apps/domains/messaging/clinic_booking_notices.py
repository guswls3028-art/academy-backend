"""Presentation of booking states inside the existing approved clinic_info memo."""
from .default_templates import DEFAULT_TEMPLATES


BOOKING_MEMOS = {
    "pending": ("승인 대기", "예약 신청이 접수되었습니다. 현재 선생님 승인 대기 중입니다."),
    "late_pending": ("마감 후 승인 대기", "마감 후 예약 신청이 접수되었습니다. 현재 선생님 승인 대기 중입니다."),
    "booked": ("예약 확정", "클리닉 예약이 확정되었습니다."),
    "approved": ("예약 확정", "선생님이 신청을 승인하여 클리닉 예약이 확정되었습니다."),
}


def booking_notice_body(saved_body, state):
    label, memo = BOOKING_MEMOS[state]
    if saved_body == DEFAULT_TEMPLATES["clinic_reservation_created"]["body"]:
        return memo
    if "#{예약상태}" in saved_body:
        return saved_body.replace("#{예약상태}", label)
    return f"{label}\n{saved_body}".rstrip()
