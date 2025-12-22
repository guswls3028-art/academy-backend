from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser, Group, Permission

from apps.api.common.models import TimestampModel


# --------------------------------------------------
# Custom User (AUTH_USER_MODEL)
# --------------------------------------------------

class User(AbstractUser):
    """
    Custom User 모델
    - AUTH_USER_MODEL = core.User
    - auth.User 와의 groups / permissions reverse accessor 충돌 방지
    """

    name = models.CharField(max_length=50, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)

    # 🔥 핵심: auth.User 와 reverse accessor 충돌 방지
    groups = models.ManyToManyField(
        Group,
        related_name="core_users",
        blank=True,
    )
    user_permissions = models.ManyToManyField(
        Permission,
        related_name="core_users",
        blank=True,
    )

    class Meta:
        app_label = "core"
        db_table = "accounts_user"
        ordering = ["-id"]

    def __str__(self):
        return self.username


# --------------------------------------------------
# Attendance
# --------------------------------------------------

class Attendance(TimestampModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attendances",
    )

    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()

    work_type = models.CharField(max_length=50)
    memo = models.TextField(blank=True, null=True)

    duration_hours = models.FloatField(default=0)
    amount = models.IntegerField(default=0)

    class Meta:
        app_label = "core"
        ordering = ["-date", "-start_time"]

    def __str__(self):
        return f"{self.user.username} - {self.date}"


# --------------------------------------------------
# Expense
# --------------------------------------------------

class Expense(TimestampModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="expenses",
    )

    date = models.DateField()
    title = models.CharField(max_length=255)
    amount = models.IntegerField()
    memo = models.TextField(blank=True, null=True)

    class Meta:
        app_label = "core"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.user.username} - {self.title}"
