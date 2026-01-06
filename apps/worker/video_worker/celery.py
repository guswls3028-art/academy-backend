# apps/worker/video_worker/celery.py

from celery import Celery

app = Celery("academy_video")

app.config_from_object(
    "django.conf:settings",
    namespace="CELERY",
)

app.conf.worker_state_db = None

# 🎬 비디오 전용 task만
app.autodiscover_tasks([
    "apps.shared.tasks.media",
])

print("🎬 Video Celery READY 🎬")
