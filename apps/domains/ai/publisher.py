# apps/domains/ai/publisher.py
from apps.shared.contracts.ai_job import AIJob


def publish_job(job: AIJob) -> None:
    """
    실제 메시지 큐 연결 지점.

    ✅ 운영 기본: DBQueue (DB가 SSOT)
    ✅ legacy/옵션: worker.queue.producer 가 있으면 그쪽도 시도 가능
    - "기존 호출부 유지"하면서 내부에서 안전하게 처리
    """
    # 1) 운영 기본: DBQueue publisher
    from apps.domains.ai.queueing.publisher import publish_ai_job_db
    publish_ai_job_db(job)

    # 2) legacy: 존재하면 추가로 publish(삭제 금지 요구 대응)
    try:
        from worker.queue.producer import publish_ai_job  # type: ignore
        publish_ai_job(job)
    except Exception:
        # legacy 경로가 없거나 실패해도 DBQueue가 SSOT이므로 무시
        return
