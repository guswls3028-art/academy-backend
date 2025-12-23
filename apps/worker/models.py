# apps/worker/models.py

from django.db import models


class Video(models.Model):
    """
    ⚠️ Worker 전용 mirror model
    - migration 없음
    - API 모델과 1:1 구조 맞춤
    - 오직 task에서 DB 접근용
    """

    class Meta:
        managed = False  # 🔥 중요: migration 안 함
        db_table = "media_video"

    class Status(models.TextChoices):
        UPLOADED = "UPLOADED"
        PROCESSING = "PROCESSING"
        READY = "READY"
        FAILED = "FAILED"

    id = models.BigAutoField(primary_key=True)

    file_key = models.CharField(max_length=255)
    status = models.CharField(max_length=20)

    duration = models.IntegerField(null=True)
    thumbnail = models.CharField(max_length=255)
    hls_path = models.CharField(max_length=255)
