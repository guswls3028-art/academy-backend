# apps/domains/ai/queueing/publisher.py
from __future__ import annotations

from apps.shared.contracts.ai_job import AIJob
from apps.domains.ai.queueing.db_queue import DBJobQueue


def publish_ai_job_db(job: AIJob) -> None:
    """
    DBQueue 발행 (운영 기본)
    - gateway에서 AIJobModel row를 생성했기 때문에 여기서는 상태만 정리
    """
    q = DBJobQueue()
    q.publish(job_id=job.id)
