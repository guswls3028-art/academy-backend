# apps/domains/ai/queueing/publisher.py
from __future__ import annotations

from apps.shared.contracts.ai_job import AIJob
from apps.domains.ai.queueing.db_queue import DBJobQueue


def publish_ai_job_db(job: AIJob) -> None:
    """
    DBQueue 발행 (운영 기본)

    - gateway에서 AIJobModel row를 선생성하고,
      여기서는 DBJobQueue.publish 로 "PENDING + next_run_at"만 세팅한다.
    - DB가 SSOT(단일진실)이며, 워커는 /internal endpoint로 pull 한다.
    """
    q = DBJobQueue()
    q.publish(job_id=str(job.id))


# ----------------------------------------------------------------------
# Backward-compat export (SSOT)
# gateway.py 등에서 publish_job 이름을 기대하는 경우가 많아 alias로 봉인한다.
# ----------------------------------------------------------------------
def publish_job(job: AIJob) -> None:
    """
    Public publisher entrypoint (SSOT).
    Keep this name stable to avoid import breaks.
    """
    publish_ai_job_db(job)
