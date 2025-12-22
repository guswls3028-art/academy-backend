from shared.contracts.ai_job import AIJob
from shared.contracts.ai_result import AIResult

from apps.worker.ai.pipelines.dispatcher import handle_ai_job
from worker.queue.producer import publish_ai_result


def consume_ai_job(message: dict):
    """
    Queue → Worker
    """
    job = AIJob(**message)

    result: AIResult = handle_ai_job(job)

    publish_ai_result(result)
