import uuid

from django.conf import settings
from django.db import models


class PublicResourceBoardAccess(models.Model):
    tenant = models.OneToOneField("core.Tenant", on_delete=models.CASCADE)
    publisher_one = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="public_resource_access_one",
    )
    publisher_two = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="public_resource_access_two",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(publisher_one__isnull=True)
                    | models.Q(publisher_two__isnull=True)
                    | ~models.Q(publisher_one=models.F("publisher_two"))
                ),
                name="resource_publishers_distinct",
            )
        ]


class PublicResourcePost(models.Model):
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    author_display_name = models.CharField(max_length=100)
    request_id = models.UUIDField(default=uuid.uuid4)
    category = models.CharField(max_length=20, choices=[("matchup", "매치업"), ("analysis", "분석자료")])
    title = models.CharField(max_length=200)
    content = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, default="published", choices=[("published", "게시됨"), ("deleted", "삭제됨")]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["tenant", "status", "category", "-created_at"], name="resource_public_list")]
        constraints = [models.UniqueConstraint(fields=["tenant", "request_id"], name="resource_request_unique")]


class PublicResourceFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    post = models.ForeignKey(PublicResourcePost, null=True, blank=True, on_delete=models.CASCADE, related_name="files")
    is_removed = models.BooleanField(default=False)
    is_ready = models.BooleanField(default=False)
    storage_key = models.CharField(max_length=500, unique=True)
    filename = models.CharField(max_length=200)
    extension = models.CharField(max_length=5)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
