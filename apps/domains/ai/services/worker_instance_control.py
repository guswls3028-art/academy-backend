# PATH: apps/domains/ai/services/worker_instance_control.py

import boto3
import logging

logger = logging.getLogger(__name__)

REGION = "ap-northeast-2"


def start_ai_worker_instance():
    """
    API 서버에서 호출
    - AI 워커 EC2를 켜기만 함
    - stop은 절대 여기서 하지 않음
    - AI_WORKER_INSTANCE_ID가 settings에 없거나 None이면 조용히 skip
    """
    from django.conf import settings

    instance_id = getattr(settings, "AI_WORKER_INSTANCE_ID", None)
    if not instance_id:
        logger.info("[AI] AI_WORKER_INSTANCE_ID not configured — skip EC2 start")
        return

    ec2 = boto3.client("ec2", region_name=REGION)
    logger.info("[AI] Starting AI worker EC2 instance: %s", instance_id)
    ec2.start_instances(InstanceIds=[instance_id])
