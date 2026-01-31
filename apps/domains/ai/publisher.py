# apps/domains/ai/publisher.py
from apps.shared.contracts.ai_job import AIJob
from django.conf import settings
import redis


QUEUE_KEY = "ai:jobs"


def publish_job(job: AIJob) -> None:
    """
    API → Queue (Redis)
    Worker는 Queue만 본다.
    """
    r = redis.from_url(settings.REDIS_URL)
    r.lpush(QUEUE_KEY, job.to_json())
