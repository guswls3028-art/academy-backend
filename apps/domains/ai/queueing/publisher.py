# apps/domains/ai/queueing/publisher.py
from __future__ import annotations

import logging
from apps.shared.contracts.ai_job import AIJob
from apps.domains.ai.models import AIJobModel

logger = logging.getLogger(__name__)


def publish_ai_job_sqs(job_model: AIJobModel) -> None:
    """
    SQS 큐에 AI 작업 발행 (Tier별 라우팅)
    
    Args:
        job_model: AIJobModel 객체 (tier 필드 포함)
    """
    from apps.support.ai.services.sqs_queue import AISQSQueue
    
    queue = AISQSQueue()
    queue.enqueue(job_model)


# ----------------------------------------------------------------------
# Backward-compat export (SSOT)
# gateway.py 등에서 publish_job 이름을 기대하는 경우가 많아 alias로 봉인한다.
# ----------------------------------------------------------------------
def publish_job(job: AIJob) -> None:
    """
    Public publisher entrypoint (SSOT).
    
    SQS 큐만 사용 (Redis 제거됨)
    Tier는 AIJobModel에서 가져옴
    """
    from apps.domains.ai.models import AIJobModel
    
    # AIJobModel에서 tier 가져오기
    job_model = AIJobModel.objects.filter(job_id=str(job.id)).first()
    if job_model:
        publish_ai_job_sqs(job_model)
    else:
        logger.warning("AIJobModel not found for job %s, using basic tier", job.id)
        # 기본값으로 basic tier 사용
        job_model = AIJobModel.objects.create(
            job_id=job.id,
            job_type=job.type,
            payload=job.payload or {},
            status="PENDING",
            tier="basic",
        )
        publish_ai_job_sqs(job_model)
