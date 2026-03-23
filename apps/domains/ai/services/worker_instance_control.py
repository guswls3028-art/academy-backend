# PATH: apps/domains/ai/services/worker_instance_control.py

import boto3
import logging

logger = logging.getLogger(__name__)

REGION = "ap-northeast-2"


AI_WORKER_ASG_NAME = "academy-v1-ai-worker-asg"


def start_ai_worker_instance():
    """
    API 서버에서 호출
    - ASG 내 AI 워커 인스턴스가 stopped이면 start
    - ASG desired=0이면 1로 올림
    - 이미 running이면 no-op (idempotent)
    """
    try:
        asg = boto3.client("autoscaling", region_name=REGION)
        resp = asg.describe_auto_scaling_groups(
            AutoScalingGroupNames=[AI_WORKER_ASG_NAME]
        )
        groups = resp.get("AutoScalingGroups", [])
        if not groups:
            logger.warning("[AI] ASG %s not found — skip", AI_WORKER_ASG_NAME)
            return

        group = groups[0]
        desired = group["DesiredCapacity"]
        instances = group.get("Instances", [])

        # ASG desired가 0이면 1로 올림
        if desired == 0:
            logger.info("[AI] ASG desired=0 → setting to 1")
            asg.set_desired_capacity(
                AutoScalingGroupName=AI_WORKER_ASG_NAME,
                DesiredCapacity=1,
            )
            return

        # 인스턴스가 있으면 stopped 상태인지 확인하여 start
        ec2 = boto3.client("ec2", region_name=REGION)
        for inst in instances:
            iid = inst["InstanceId"]
            ec2_resp = ec2.describe_instance_status(
                InstanceIds=[iid], IncludeAllInstances=True
            )
            statuses = ec2_resp.get("InstanceStatuses", [])
            if statuses and statuses[0]["InstanceState"]["Name"] == "stopped":
                logger.info("[AI] Starting stopped AI worker: %s", iid)
                ec2.start_instances(InstanceIds=[iid])
                return

        logger.info("[AI] AI worker already running (instances=%d, desired=%d)", len(instances), desired)
    except Exception:
        logger.warning("[AI] AI 워커 기동 시도 실패 — job은 SQS에 정상 등록됨", exc_info=True)
