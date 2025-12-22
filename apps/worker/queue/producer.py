from shared.contracts.ai_job import AIJob
from shared.contracts.ai_result import AIResult


def publish_ai_job(job: AIJob) -> None:
    """
    API → Worker
    실제로는 Redis/SQS/Kafka
    """
    print("[QUEUE] publish job", job)


def publish_ai_result(result: AIResult) -> None:
    """
    Worker → API
    """
    print("[QUEUE] publish result", result)
