# PATH: apps/domains/clinic/models.py

from django.db import models
from django.conf import settings

from apps.api.common.models import TimestampModel
from apps.domains.students.models import Student


# --------------------------------------------------
# Clinic Session
# --------------------------------------------------

class Session(TimestampModel):
    date = models.DateField()
    start_time = models.TimeField()

    # ✅ [ADD] 수업 소요 시간 (분 단위)
    # - 종료시간은 저장하지 않음 (파생값)
    # - 기존 데이터 안정성 확보를 위해 default=60
    duration_minutes = models.PositiveIntegerField(default=60)

    location = models.CharField(max_length=255)
    max_participants = models.PositiveIntegerField()

    # ✅ [ADD] 생성자 (운영/감사 추적용)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_clinic_sessions",
    )

    def __str__(self):
        return f"{self.date} {self.start_time} @ {self.location}"


# --------------------------------------------------
# Session Participant (✅ SaaS 운영 상태 확장)
# --------------------------------------------------

class SessionParticipant(TimestampModel):
    class Status(models.TextChoices):
        BOOKED = "booked", "Booked"         # 예약됨
        ATTENDED = "attended", "Attended"   # 출석(수업 완료)
        NO_SHOW = "no_show", "NoShow"       # 미이행(예약했지만 수업 성립 X)
        CANCELLED = "cancelled", "Cancelled"  # 취소

    class Source(models.TextChoices):
        AUTO = "auto", "Auto"       # 자동(Results 판정)
        MANUAL = "manual", "Manual" # 수동 등록

    class Reason(models.TextChoices):
        EXAM = "exam", "Exam"
        HOMEWORK = "homework", "Homework"
        BOTH = "both", "Both"

    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="clinic_participations",
    )

    # ✅ 운영 상태
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.BOOKED,
    )

    # ✅ Results 연계 메타 (판정은 results 단일진실, clinic은 “기록/추적”만)
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

    # ✅ [ADD] 대상자 / 수동 구분
    participant_role = models.CharField(
        max_length=20,
        choices=(("target", "Target"), ("manual", "Manual")),
        default="target",
    )

    # ✅ [ADD] 상태 변경 로그
    status_changed_at = models.DateTimeField(null=True, blank=True)
    status_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="clinic_participant_status_changes",
    )

    # ✅ [ADD] 출석 확장 대비
    checked_in_at = models.DateTimeField(null=True, blank=True)
    is_late = models.BooleanField(default=False)

    memo = models.TextField(blank=True, null=True)

    class Meta:
        unique_together = ("session", "student")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student.name} in {self.session} ({self.status})"


# --------------------------------------------------
# Clinic Test
# --------------------------------------------------

class Test(TimestampModel):
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

    def __str__(self):
        return f"{self.title} ({self.round}차)"


# --------------------------------------------------
# Submission Upload Path
# --------------------------------------------------

def submission_upload_path(instance, filename):
    return f"clinic/submissions/{instance.student_id}/{instance.test_id}/{filename}"


# --------------------------------------------------
# Submission
# --------------------------------------------------

class Submission(TimestampModel):
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

    # ✅ [ADD] 채점 완료 시각
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("test", "student")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student.name} - {self.test.title}"
