from django.db import models

from apps.api.common.models import TimestampModel
from apps.domains.students.models import Student


# --------------------------------------------------
# Clinic Session
# --------------------------------------------------

class Session(TimestampModel):
    date = models.DateField()
    start_time = models.TimeField()
    location = models.CharField(max_length=255)
    max_participants = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.date} {self.start_time} @ {self.location}"


# --------------------------------------------------
# Session Participant
# --------------------------------------------------

class SessionParticipant(TimestampModel):
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

    status = models.CharField(
        max_length=20,
        choices=(
            ("registered", "Registered"),
            ("cancelled", "Cancelled"),
        ),
        default="registered",
    )
    memo = models.TextField(blank=True, null=True)

    class Meta:
        unique_together = ("session", "student")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student.name} in {self.session}"


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

    class Meta:
        unique_together = ("test", "student")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student.name} - {self.test.title}"
