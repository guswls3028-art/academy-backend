import uuid

from django.conf import settings
from django.db import models


class PublicVideo(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE)
    youtube_video_id = models.CharField(max_length=11)
    canonical_url = models.URLField(max_length=200)
    source_title = models.CharField(max_length=500, blank=True)
    channel_title = models.CharField(max_length=200, blank=True)
    thumbnail_url = models.URLField(max_length=500, blank=True)
    metadata_checked_at = models.DateTimeField(null=True, blank=True)
    draft_content = models.JSONField(default=dict)
    published_content = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=12, default="draft", choices=[
        ("draft", "초안"), ("published", "게시 중"), ("unpublished", "게시 중단"),
    ])
    revision = models.PositiveIntegerField(default=1)
    request_id = models.UUIDField()
    creation_content = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="public_videos_created")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="public_videos_updated")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "youtube_video_id"], name="public_video_tenant_youtube"),
            models.UniqueConstraint(fields=["tenant", "request_id"], name="public_video_tenant_request"),
        ]
        indexes = [models.Index(fields=["tenant", "status", "-published_at"], name="public_video_published_idx")]


class PublicVideoShowcase(models.Model):
    tenant = models.OneToOneField("core.Tenant", on_delete=models.CASCADE)
    hero_video = models.ForeignKey(PublicVideo, null=True, blank=True, on_delete=models.SET_NULL)
    revision = models.PositiveIntegerField(default=0)


class PublicVideoEvent(models.Model):
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE)
    video = models.ForeignKey(PublicVideo, null=True, on_delete=models.SET_NULL)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=20)
    video_revision = models.PositiveIntegerField(default=0)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
