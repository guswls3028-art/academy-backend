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
    message_body = models.TextField(blank=True, default="", help_text="실제 발송된 메시지 본문")
    message_mode = models.CharField(
        max_length=20, blank=True, default="",
        choices=[("sms", "SMS"), ("alimtalk", "알림톡")],
        help_text="발송 방식",
    )
    sqs_message_id = models.CharField(
        max_length=128, blank=True, default="", db_index=True,
        help_text="SQS MessageId for dedup (empty for legacy logs)",
    )
    business_idempotency_key = models.CharField(
        max_length=64, blank=True, default="",
        help_text="SHA-256 hash of business dedup key (tenant+channel+event+target+recipient). Empty for legacy.",
    )
    status = models.CharField(
        max_length=20, blank=True, default="sent",
        choices=[
            ("processing", "처리중"),
            ("sent", "발송완료"),
            ("failed", "실패"),
        ],
        help_text="발송 상태. processing=선점됨, sent=발송완료, failed=실패",
    )
    claimed_at = models.DateTimeField(null=True, blank=True, help_text="Worker 선점 시각")
    batch_id = models.UUIDField(null=True, blank=True, db_index=True, help_text="수동 발송 배치 ID")
    sender_staff_id = models.IntegerField(null=True, blank=True, help_text="발송 요청 Staff ID")
    notification_type = models.CharField(max_length=30, blank=True, default="", help_text="check_in, absent 등")

    class Meta:
        app_label = "messaging"
        ordering = ["-sent_at"]
        verbose_name = "Notification log"
        verbose_name_plural = "Notification logs"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "message_mode", "business_idempotency_key"],
                condition=models.Q(business_idempotency_key__gt=""),
                name="uniq_notification_business_key_per_tenant_channel",
            ),
        ]


class NotificationPreviewToken(models.Model):
    """
    수동 알림 발송의 preview → confirm 핸드셰이크 토큰.
    preview 시 생성, confirm 시 소비 (1회용).
    """
    token = models.UUIDField(unique=True, db_index=True)
    tenant = models.ForeignKey(
        "core.Tenant", on_delete=models.CASCADE,
        related_name="notification_preview_tokens",
    )
    notification_type = models.CharField(
        max_length=30,
        help_text="check_in | absent | clinic_reminder 등",
    )
    session_type = models.CharField(
        max_length=20,
        help_text="attendance | clinic",
    )
    session_id = models.IntegerField()
    send_to = models.CharField(max_length=20, default="parent")
    payload = models.JSONField(help_text="직렬화된 수신자 + 메시지 데이터")
    created_by_id = models.IntegerField(null=True, blank=True, help_text="Staff ID")
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True, help_text="소비 시각 (non-null=사용됨)")
    expires_at = models.DateTimeField()
    batch_id = models.UUIDField(null=True, blank=True)

    class Meta:
        app_label = "messaging"
        verbose_name = "Notification preview token"
        verbose_name_plural = "Notification preview tokens"


class MessageTemplate(models.Model):
    """
    메시지 양식 템플릿 — 테넌트별 저장, 카테고리별 사용처 구분
    - default: 기본(어디서나), 기본 블록만
    - lecture: 강의·차시(세션) 내 학생 선택 발송용
    - clinic: 클리닉 내 학생 선택 발송용
    """
    class Category(models.TextChoices):
        DEFAULT = "default", "기본"
        SIGNUP = "signup", "가입/등록"
        ATTENDANCE = "attendance", "출결"
        LECTURE = "lecture", "강의"
        EXAM = "exam", "시험"
        ASSIGNMENT = "assignment", "과제"
        GRADES = "grades", "성적"
        CLINIC = "clinic", "클리닉"
        PAYMENT = "payment", "결제"
        NOTICE = "notice", "운영공지"
        COMMUNITY = "community", "커뮤니티"
        STAFF = "staff", "직원"

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

    is_system = models.BooleanField(
        default=False,
        help_text="시스템 기본 양식 여부. True이면 사용자 수정/삭제 불가.",
    )
    is_user_default = models.BooleanField(
        default=False,
        help_text="사용자가 해당 카테고리에서 기본 양식으로 지정한 템플릿. tenant+category당 1개만.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "messaging"
        ordering = ["-updated_at"]
        verbose_name = "Message template"
        verbose_name_plural = "Message templates"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "category"],
                condition=models.Q(is_user_default=True),
                name="uniq_user_default_per_tenant_category",
            ),
        ]


class AutoSendConfig(models.Model):
    """
    자동발송 설정 — 이벤트(트리거)별 템플릿·발송 조건.
    SSOT: backend/docs/AUTO-SEND-EVENT-SPEC.md
    """
    class Trigger(models.TextChoices):
        # A. 가입/등록 — SYSTEM_AUTO
        STUDENT_SIGNUP = "student_signup", "가입 완료(레거시·미사용)"
        REGISTRATION_APPROVED_STUDENT = "registration_approved_student", "가입 안내(학생)"
        REGISTRATION_APPROVED_PARENT = "registration_approved_parent", "가입 안내(학부모)"
        CLASS_ENROLLMENT_COMPLETE = "class_enrollment_complete", "반 등록 완료"
        ENROLLMENT_EXPIRING_SOON = "enrollment_expiring_soon", "등록 만료 예정"
        WITHDRAWAL_COMPLETE = "withdrawal_complete", "퇴원 처리 완료"
        # B. 출결 (일반 강의) — MANUAL_DEFAULT / DISABLED
        LECTURE_SESSION_REMINDER = "lecture_session_reminder", "수업 시작 N분 전"
        CHECK_IN_COMPLETE = "check_in_complete", "입실 완료(일반 강의)"
        ABSENT_OCCURRED = "absent_occurred", "결석 발생(일반 강의)"
        # C. 시험 — MANUAL_DEFAULT
        EXAM_SCHEDULED_DAYS_BEFORE = "exam_scheduled_days_before", "시험 예정 N일 전"
        EXAM_START_MINUTES_BEFORE = "exam_start_minutes_before", "시험 시작 N분 전"
        EXAM_NOT_TAKEN = "exam_not_taken", "시험 미응시"
        EXAM_SCORE_PUBLISHED = "exam_score_published", "성적 공개"
        RETAKE_ASSIGNED = "retake_assigned", "재시험 대상 지정"
        # D. 과제 — MANUAL_DEFAULT
        ASSIGNMENT_REGISTERED = "assignment_registered", "과제 등록"
        ASSIGNMENT_DUE_HOURS_BEFORE = "assignment_due_hours_before", "과제 마감 N시간 전"
        ASSIGNMENT_NOT_SUBMITTED = "assignment_not_submitted", "과제 미제출"
        # E. 성적/리포트 — MANUAL_DEFAULT
        MONTHLY_REPORT_GENERATED = "monthly_report_generated", "월간 성적 리포트 발송"
        # F. 클리닉/상담 — AUTO_DEFAULT
        CLINIC_REMINDER = "clinic_reminder", "클리닉 시작 N분 전"
        CLINIC_RESERVATION_CREATED = "clinic_reservation_created", "클리닉 예약 완료"
        CLINIC_RESERVATION_CHANGED = "clinic_reservation_changed", "클리닉 예약 변경"
        CLINIC_CANCELLED = "clinic_cancelled", "클리닉 예약 취소"
        CLINIC_CHECK_IN = "clinic_check_in", "클리닉 입실"
        CLINIC_CHECK_OUT = "clinic_check_out", "클리닉 퇴실(완료)"
        CLINIC_ABSENT = "clinic_absent", "클리닉 결석"
        CLINIC_SELF_STUDY_COMPLETED = "clinic_self_study_completed", "자율학습 완료"
        CLINIC_RESULT_NOTIFICATION = "clinic_result_notification", "클리닉 대상 해소(완료)"
        COUNSELING_RESERVATION_CREATED = "counseling_reservation_created", "상담 예약 완료"
        # G. 결제
        PAYMENT_COMPLETE = "payment_complete", "결제 완료"
        PAYMENT_DUE_DAYS_BEFORE = "payment_due_days_before", "납부 예정일 N일 전"
        # J. 영상
        VIDEO_ENCODING_COMPLETE = "video_encoding_complete", "영상 인코딩 완료"
        # H. 운영공지 — urgent_notice 제거 (카카오 알림톡 정책 위반)
        # I. 비밀번호 찾기/재설정 — SYSTEM_AUTO
        PASSWORD_FIND_OTP = "password_find_otp", "비밀번호 찾기 인증번호"
        PASSWORD_RESET_STUDENT = "password_reset_student", "비밀번호 재설정(학생)"
        PASSWORD_RESET_PARENT = "password_reset_parent", "비밀번호 재설정(학부모)"

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
        choices=[("sms", "SMS만"), ("alimtalk", "알림톡만"), ("both", "둘 다")],
        default="alimtalk",
    )
    # N분 전 발송 (예: 강의 30분 전, 클리닉 60분 전). null=이벤트 시점 발송. 스케줄러에서 사용.
    minutes_before = models.PositiveIntegerField(null=True, blank=True)
    # 발송 시점 모드: immediate=즉시, delay_minutes=N분 후, scheduled_hour=매일 지정 시각
    delay_mode = models.CharField(
        max_length=20,
        choices=[
            ("immediate", "즉시 발송"),
            ("delay_minutes", "N분 후 발송"),
            ("scheduled_hour", "지정 시각 발송"),
        ],
        default="immediate",
    )
    # delay_minutes 모드: 발송 지연 분 수 (예: 60 → 1시간 후)
    # scheduled_hour 모드: 발송 시각 (0~23, 예: 7 → 오전 7시)
    delay_value = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "messaging"
        unique_together = [("tenant", "trigger")]
        verbose_name = "Auto-send config"
        verbose_name_plural = "Auto-send configs"


class ScheduledNotification(models.Model):
    """
    예약/지연 발송 대기열.
    delay_mode가 immediate가 아닌 경우, 발송 시점을 계산해 여기에 저장.
    주기적 폴러(management command)가 send_at <= now인 건을 처리.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "대기"
        SENT = "sent", "발송완료"
        FAILED = "failed", "실패"
        CANCELLED = "cancelled", "취소"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="scheduled_notifications",
        db_index=True,
    )
    trigger = models.CharField(max_length=60, db_index=True)
    send_at = models.DateTimeField(db_index=True)
    payload = models.JSONField(help_text="enqueue_sms kwargs (to, text, message_mode, template_id, etc.)")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        app_label = "messaging"
        ordering = ["send_at"]
        verbose_name = "Scheduled notification"
        verbose_name_plural = "Scheduled notifications"
        indexes = [
            models.Index(fields=["status", "send_at"], name="idx_sched_notif_status_sendat"),
        ]
