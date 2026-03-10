# apps/support/messaging/models.py
"""
알림톡 발송 로그 · 메시지 템플릿 · 자동발송 설정
"""

from decimal import Decimal

from django.db import models


class NotificationLog(models.Model):
    """
    발송 1건당 1행. 워커가 Solapi 호출 후 성공 시 차감·기록, 실패 시 롤백 후 기록(선택).
    """

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="notification_logs",
        db_index=True,
    )
    sent_at = models.DateTimeField(auto_now_add=True, db_index=True)
    success = models.BooleanField(default=False)
    amount_deducted = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0")
    )
    recipient_summary = models.CharField(max_length=500, blank=True, default="")
    template_summary = models.CharField(max_length=255, blank=True, default="")
    failure_reason = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        app_label = "messaging"
        ordering = ["-sent_at"]
        verbose_name = "Notification log"
        verbose_name_plural = "Notification logs"


class MessageTemplate(models.Model):
    """
    메시지 양식 템플릿 — 테넌트별 저장, 카테고리별 사용처 구분
    - default: 기본(어디서나), 기본 블록만
    - lecture: 강의·차시(세션) 내 학생 선택 발송용
    - clinic: 클리닉 내 학생 선택 발송용
    """
    class Category(models.TextChoices):
        DEFAULT = "default", "기본"
        LECTURE = "lecture", "강의"
        CLINIC = "clinic", "클리닉"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="message_templates",
        db_index=True,
    )
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.DEFAULT,
        db_index=True,
    )
    name = models.CharField(max_length=120, help_text="템플릿 이름")
    subject = models.CharField(max_length=200, blank=True, default="", help_text="제목(선택)")
    body = models.TextField(help_text="본문")

    # 솔라피 알림톡 검수 신청 연동
    solapi_template_id = models.CharField(max_length=100, blank=True, default="")
    solapi_status = models.CharField(
        max_length=20,
        blank=True,
        default="",
        choices=[
            ("", "미신청"),
            ("PENDING", "검수 대기"),
            ("APPROVED", "승인"),
            ("REJECTED", "반려"),
        ],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "messaging"
        ordering = ["-updated_at"]
        verbose_name = "Message template"
        verbose_name_plural = "Message templates"


class AutoSendConfig(models.Model):
    """
    자동발송 설정 — 이벤트(트리거)별 템플릿·발송 조건.
    SSOT: backend/docs/AUTO-SEND-EVENT-SPEC.md
    """
    class Trigger(models.TextChoices):
        # A. 가입/등록
        STUDENT_SIGNUP = "student_signup", "가입 완료"
        REGISTRATION_APPROVED_STUDENT = "registration_approved_student", "가입 승인(학생)"
        REGISTRATION_APPROVED_PARENT = "registration_approved_parent", "가입 승인(학부모)"
        CLASS_ENROLLMENT_COMPLETE = "class_enrollment_complete", "반 등록 완료"
        ENROLLMENT_EXPIRING_SOON = "enrollment_expiring_soon", "등록 만료 예정"
        WITHDRAWAL_COMPLETE = "withdrawal_complete", "퇴원 처리 완료"
        # B. 출결
        LECTURE_SESSION_REMINDER = "lecture_session_reminder", "수업 시작 N분 전"
        CHECK_IN_COMPLETE = "check_in_complete", "입실 완료"
        ABSENT_OCCURRED = "absent_occurred", "결석 발생"
        # C. 시험
        EXAM_SCHEDULED_DAYS_BEFORE = "exam_scheduled_days_before", "시험 예정 N일 전"
        EXAM_START_MINUTES_BEFORE = "exam_start_minutes_before", "시험 시작 N분 전"
        EXAM_NOT_TAKEN = "exam_not_taken", "시험 미응시"
        EXAM_SCORE_PUBLISHED = "exam_score_published", "성적 공개"
        RETAKE_ASSIGNED = "retake_assigned", "재시험 대상 지정"
        # D. 과제
        ASSIGNMENT_REGISTERED = "assignment_registered", "과제 등록"
        ASSIGNMENT_DUE_HOURS_BEFORE = "assignment_due_hours_before", "과제 마감 N시간 전"
        ASSIGNMENT_NOT_SUBMITTED = "assignment_not_submitted", "과제 미제출"
        # E. 성적/리포트
        MONTHLY_REPORT_GENERATED = "monthly_report_generated", "월간 성적 리포트 발송"
        # F. 클리닉/상담
        CLINIC_REMINDER = "clinic_reminder", "클리닉 시작 N분 전"
        CLINIC_RESERVATION_CREATED = "clinic_reservation_created", "클리닉 예약 완료"
        CLINIC_RESERVATION_CHANGED = "clinic_reservation_changed", "클리닉 예약 변경"
        COUNSELING_RESERVATION_CREATED = "counseling_reservation_created", "상담 예약 완료"
        # G. 결제
        PAYMENT_COMPLETE = "payment_complete", "결제 완료"
        PAYMENT_DUE_DAYS_BEFORE = "payment_due_days_before", "납부 예정일 N일 전"
        # H. 운영공지
        URGENT_NOTICE = "urgent_notice", "긴급 공지"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="auto_send_configs",
        db_index=True,
    )
    trigger = models.CharField(
        max_length=60,
        choices=Trigger.choices,
        db_index=True,
    )
    template = models.ForeignKey(
        MessageTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auto_send_configs",
    )
    enabled = models.BooleanField(default=False)
    message_mode = models.CharField(
        max_length=20,
        choices=[("sms", "SMS만"), ("alimtalk", "알림톡만"), ("both", "알림톡→SMS폴백")],
        default="sms",
    )
    # N분 전 발송 (예: 강의 30분 전, 클리닉 60분 전). null=이벤트 시점 발송. 스케줄러에서 사용.
    minutes_before = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "messaging"
        unique_together = [("tenant", "trigger")]
        verbose_name = "Auto-send config"
        verbose_name_plural = "Auto-send configs"
