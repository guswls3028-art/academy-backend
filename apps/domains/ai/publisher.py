# apps/domains/ai/publisher.py
from apps.shared.contracts.ai_job import AIJob

def publish_job(job: AIJob) -> None:
    """
    실제 메시지 큐 연결 지점.
    지금은 infra layer placeholder.
    """
    from worker.queue.producer import publish_ai_job
    publish_ai_job(job)
