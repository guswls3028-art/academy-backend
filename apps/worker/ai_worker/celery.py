# apps/worker/ai_worker/celery.py

from celery import Celery

app = Celery("academy_ai")

app.conf.broker_url = "redis://172.31.32.109:6379/0"
app.conf.result_backend = "redis://172.31.32.109:6379/0"

# 🤖 AI 전용
app.autodiscover_tasks([
    "apps.worker.ai_worker.ai",
])

print("🤖 AI Celery READY 🤖")
