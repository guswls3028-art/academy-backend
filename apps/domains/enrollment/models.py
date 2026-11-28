# PATH: apps/domains/enrollment/models.py

from django.db import models

from apps.api.common.models import TimestampModel
from apps.core.models import Tenant


# ========================================================
# Enrollment (강의 단위 수강 등록)
# ========================================================

class Enrollment(TimestampModel):
    """
    학생이 특정 강의를 수강하는 행위.
    강의 정의(Lecture)와 분리된 '수강 행위' 도메인이다.

    ✅ 운영 기준:
    - Enrollment는 반드시 tenant 단위로 격리됨
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="enrollments",
        null=False,  # ✅ NOT NULL로 변경 (프로덕션 준비)
        blank=False,
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    lecture = models.ForeignKey(
        "lectures.Lecture",
        on_delete=models.CASCADE,
        related_name="enrollments",
    )

    status = models.CharField(
        max_length=20,
        choices=[
            ("ACTIVE", "활성"),
            ("INACTIVE", "비활성"),
            ("PENDING", "대기"),
        ],
        default="ACTIVE",
    )

    status_before_student_deletion = models.CharField(
        max_length=20,
        choices=[
            ("ACTIVE", "활성"),
            ("INACTIVE", "비활성"),
            ("PENDING", "대기"),
        ],
        null=True,
        blank=True,
        editable=False,
        help_text=(
            "학생 소프트 삭제 직전 수강 상태. 학생 복원 시 한 번 사용하고 "
            "다시 비웁니다."
        ),
    )

    enrolled_at = models.DateTimeField(auto_now_add=True)

    lecture_memo = models.TextField(blank=True, default="", db_default="")

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "created_at"]),  # ✅ 복합 인덱스 추가
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "student", "lecture"],
                name="unique_enrollment_per_tenant_lecture",
            )
        ]

    def __str__(self):
        return f"{self.student.name} -> {self.lecture.title}"


# ========================================================
# SessionEnrollment (차시 단위 수강 권한)
# ========================================================

class SessionEnrollment(models.Model):
    """
    특정 Enrollment가 어떤 Session(차시)에 접근 가능한지 정의.
    출결/영상/자료 접근의 기준이 되는 중간 테이블.
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="session_enrollments",
        null=False,  # ✅ NOT NULL로 변경 (프로덕션 준비)
        blank=False,
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    session = models.ForeignKey(
        "lectures.Session",
        on_delete=models.CASCADE,
        related_name="session_enrollments",
    )
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.CASCADE,
        related_name="session_enrollments",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("tenant", "session", "enrollment")

    def __str__(self):
        return f"{self.session} - {self.enrollment.student.name}"


class EnrollmentNoticeRegistration(models.Model):
    """One staff registration decision, replayable without repeating delivery."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    lecture = models.ForeignKey("lectures.Lecture", on_delete=models.CASCADE)
    actor_id = models.PositiveBigIntegerField()
    request_id = models.UUIDField()
    student_ids = models.JSONField()
    student_notice = models.CharField(max_length=8)
    parent_notice = models.CharField(max_length=8)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "actor_id", "request_id"], name="enroll_notice_request_uniq"),
        ]
        indexes = [
            models.Index(fields=["tenant", "lecture", "actor_id", "-id"], name="enroll_notice_latest"),
        ]


class EnrollmentNoticeRecipient(models.Model):
    """An individual student's role decision. Contains no phone or credentials."""

    registration = models.ForeignKey(EnrollmentNoticeRegistration, on_delete=models.CASCADE, related_name="recipients")
    student_id = models.PositiveBigIntegerField()
    role = models.CharField(max_length=8)
    status = models.CharField(max_length=20)
    recipient_fingerprint = models.CharField(max_length=64, blank=True)
    initial_notice_since = models.DateTimeField(null=True)
    source_origin_type = models.CharField(max_length=64, blank=True)
    source_origin_id = models.CharField(max_length=128, blank=True)
    account_id = models.PositiveBigIntegerField(null=True)
    outbox_id = models.PositiveBigIntegerField(null=True)
    business_key = models.CharField(max_length=64, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["registration", "student_id", "role"], name="enroll_notice_role_uniq"),
        ]
        indexes = [
            models.Index(fields=["status", "next_attempt_at", "id"], name="enroll_notice_due"),
        ]
