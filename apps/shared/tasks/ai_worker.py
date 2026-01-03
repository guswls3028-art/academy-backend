# apps/shared/tasks/ai_worker.py
from __future__ import annotations

from celery import shared_task

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult
from apps.worker.ai.pipelines.dispatcher import handle_ai_job


@shared_task(
    bind=True,
    queue="ai",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
)
def run_ai_job_task(self, job_dict: dict) -> dict:
    """
    API → AI Worker 단일 진입점 (MVP)

    역할:
    - AIJob 계약(dict)을 받아서
    - worker-side handle_ai_job 실행
    - AIResult 계약(dict)으로 반환

    원칙:
    - DB 접근 ❌
    - Django ORM ❌
    - 파일은 path 기준으로만 처리
    """

    # 1️⃣ Contract 복원
    job = AIJob.from_dict(job_dict)

    try:
        # 2️⃣ 실제 AI 처리 (worker pure logic)
        result: AIResult = handle_ai_job(job)

    except Exception as e:
        # 🚨 여기서 raise하면 Celery retry가 걸림
        raise RuntimeError(
            f"AI worker failed (job_id={job.id}, type={job.type}): {e}"
        ) from e

    # 3️⃣ Contract → dict 반환
    return result.to_dict()
