# apps/support/messaging/services.py

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
