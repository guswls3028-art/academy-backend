from datetime import datetime, timedelta

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .time_ranges import booking_window, session_window


def cutoff_settings(tenant):
    return {
        "booking_cutoff_mode": tenant.clinic_booking_cutoff_mode,
        "booking_cutoff_time": tenant.clinic_booking_cutoff_time.strftime("%H:%M"),
        "booking_cutoff_minutes": tenant.clinic_booking_cutoff_minutes,
        "allow_late_requests": tenant.clinic_allow_late_requests,
    }


def booking_cutoff_at(tenant, start):
    mode = tenant.clinic_booking_cutoff_mode
    if mode == "none":
        return None
    if mode == "previous_day":
        deadline = datetime.combine(start.date() - timedelta(days=1), tenant.clinic_booking_cutoff_time)
    elif mode == "before_start":
        deadline = start - timedelta(minutes=tenant.clinic_booking_cutoff_minutes)
    else:
        raise ValidationError({"booking_cutoff_mode": "예약 마감 설정을 확인해 주세요."})
    return timezone.make_aware(deadline, timezone.get_default_timezone())


def student_booking_status(tenant, session, start_time, end_time):
    """Use the chosen arrival in range mode; staff writes never enter this policy."""
    from .models import SessionParticipant

    auto_status = SessionParticipant.Status.BOOKED if tenant.clinic_auto_approve_booking else SessionParticipant.Status.PENDING
    if tenant.clinic_booking_cutoff_mode == "none":
        return auto_status, False
    start, _ = (
        booking_window(session=session, start_time=start_time, end_time=end_time)
        if start_time is not None else session_window(session)
    )
    now = timezone.now()
    if now < booking_cutoff_at(tenant, start):
        return auto_status, False
    if now >= timezone.make_aware(start, timezone.get_default_timezone()):
        raise ValidationError({"booking_cutoff": "이미 시작한 시간대는 신청할 수 없습니다."})
    if not tenant.clinic_allow_late_requests:
        raise ValidationError({"booking_cutoff": "학생 예약이 마감되었습니다."})
    return SessionParticipant.Status.PENDING, True
