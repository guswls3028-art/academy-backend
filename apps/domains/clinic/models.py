# PATH: apps/domains/clinic/models.py

from django.db import models
from django.conf import settings

from apps.api.common.models import TimestampModel
from apps.domains.students.models import Student
from apps.core.models import Tenant


# --------------------------------------------------
# Clinic Session
# --------------------------------------------------

class Session(TimestampModel):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="clinic_sessions",
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    date = models.DateField()
    start_time = models.TimeField()

    duration_minutes = models.PositiveIntegerField(default=60)

    location = models.CharField(max_length=255)
    max_participants = models.PositiveIntegerField()

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_clinic_sessions",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "date", "start_time", "location"],
                name="uniq_clinic_session_per_tenant_time_location",
            )
        ]

    def __str__(self):
        return f"{self.date} {self.start_time} @ {self.location}"


# --------------------------------------------------
# Session Participant
# --------------------------------------------------

class SessionParticipant(TimestampModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"  # 학생 예약 신청 대기
        BOOKED = "booked", "Booked"
        ATTENDED = "attended", "Attended"
        NO_SHOW = "no_show", "NoShow"
        CANCELLED = "cancelled", "Cancelled"
        REJECTED = "rejected", "Rejected"  # 선생이 거부

    class Source(models.TextChoices):
        AUTO = "auto", "Auto"
        MANUAL = "manual", "Manual"
        STUDENT_REQUEST = "student_request", "Student Request"  # 학생 신청

    class Reason(models.TextChoices):
        EXAM = "exam", "Exam"
        HOMEWORK = "homework", "Homework"
        BOTH = "both", "Both"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="clinic_participants",
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="participants",
        null=True,  # ✅ 학생 신청 시 세션이 없을 수 있음
        blank=True,
    )
    
    # ✅ 학생 신청 시 요청한 날짜/시간 (세션이 없을 때 사용)
    requested_date = models.DateField(null=True, blank=True)
    requested_start_time = models.TimeField(null=True, blank=True)
    
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="clinic_participations",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.BOOKED,
    )

    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.AUTO,
    )
    enrollment_id = models.PositiveIntegerField(null=True, blank=True)
    clinic_reason = models.CharField(
        max_length=20,
        choices=Reason.choices,
        null=True,
        blank=True,
    )

    participant_role = models.CharField(
        max_length=20,
        choices=(("target", "Target"), ("manual", "Manual")),
        default="target",
    )

    status_changed_at = models.DateTimeField(null=True, blank=True)
    status_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="clinic_participant_status_changes",
    )

    checked_in_at = models.DateTimeField(null=True, blank=True)
    is_late = models.BooleanField(default=False)

    memo = models.TextField(blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "session", "student"],
                name="uniq_clinic_participant_per_tenant",
            )
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student.name} in {self.session} ({self.status})"


# --------------------------------------------------
# Clinic Test
# --------------------------------------------------

class Test(TimestampModel):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="clinic_tests",
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="tests",
    )
    title = models.CharField(max_length=255)
    round = models.PositiveIntegerField(default=1)
    date = models.DateField()

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "session", "round"],
                name="uniq_clinic_test_per_tenant_session_round",
            )
        ]

    def __str__(self):
        return f"{self.title} ({self.round}차)"


# --------------------------------------------------
# Submission
# --------------------------------------------------

def submission_upload_path(instance, filename):
    return f"clinic/submissions/{instance.student_id}/{instance.test_id}/{filename}"


class Submission(TimestampModel):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="clinic_submissions",
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    test = models.ForeignKey(
        Test,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="clinic_submissions",
    )

    file = models.FileField(upload_to=submission_upload_path, null=True, blank=True)
    score = models.FloatField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=(
            ("pending", "Pending"),
            ("passed", "Passed"),
            ("failed", "Failed"),
        ),
        default="pending",
    )
    remark = models.TextField(blank=True, null=True)
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "test", "student"],
                name="uniq_clinic_submission_per_tenant",
            )
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student.name} - {self.test.title}"
