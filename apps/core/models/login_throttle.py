from django.db import models


class LoginThrottleBucket(models.Model):
    """Shared fixed-window login throttle state with no plaintext identity."""

    class Scope(models.TextChoices):
        IP = "ip", "IP"
        ACCOUNT = "account", "Account"

    bucket_key = models.CharField(max_length=64, primary_key=True)
    scope = models.CharField(max_length=16, choices=Scope.choices)
    request_count = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField()
    expires_at = models.DateTimeField(db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "core_login_throttle_bucket"
