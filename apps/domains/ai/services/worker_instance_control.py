# PATH: apps/domains/ai/services/worker_instance_control.py

import boto3
import logging

logger = logging.getLogger(__name__)

REGION = "ap-northeast-2"
AI_WORKER_INSTANCE_ID = "i-0f52f9d89481385a8"  # 네 실제 워커 EC2

def start_ai_worker_instance():
    """
    API 서버에서 호출
    - AI 워커 EC2를 켜기만 함
    - stop은 절대 여기서 하지 않음
    """
    ec2 = boto3.client("ec2", region_name=REGION)

    logger.info("[AI] Starting AI worker EC2 instance")

    ec2.start_instances(
        InstanceIds=[AI_WORKER_INSTANCE_ID]
    )
