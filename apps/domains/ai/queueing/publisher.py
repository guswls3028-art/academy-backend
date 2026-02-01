# apps/domains/ai/queueing/publisher.py
from __future__ import annotations

"""
SSOT: AI Job Publish Entry

규칙:
- gateway.py 는 publish_job 만 호출한다.
- queue 구현(DB / Redis 등)은 여기서만 결정한다.
- 현재 운영 기준은 DBJobQueue.
"""

from apps.shared.contracts.ai_job import AIJob
from apps.domains.ai.queueing.db_queue import DBJobQueue


def publish_job(job: AIJob) -> None:
    """
    ✅ 단일 진실 (SSOT)

    gateway 에서 AIJobModel row는 이미 생성됨.
    여기서는 queue 에 '실행 가능 상태'로 노출시키는 역할만 담당.
    """
    q = DBJobQueue()
    q.publish(job_id=job.id)


# ------------------------------------------------------------------
# Backward compatibility (절대 삭제 금지)
# ------------------------------------------------------------------
# 과거 코드 / legacy / 실험 브랜치 보호용 alias
publish_ai_job_db = publish_job
