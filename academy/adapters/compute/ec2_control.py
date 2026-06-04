# PATH: academy/adapters/compute/ec2_control.py
"""
AI Worker ASG/EC2 제어 어댑터.

이관 이력:
- 2026-05-12: `apps/domains/ai/services/worker_instance_control.py` 에서 이관.
  헥사고날 §6: `apps/domains/<x>/` boto3 직접 호출 금지 정책 준수.
"""
import os
import boto3
import logging

logger = logging.getLogger(__name__)

REGION = "ap-northeast-2"


AI_WORKER_ASG_NAME = "academy-v1-ai-worker-asg"


def _aws_client(service: str):
    """AWS 클라이언트 생성. ROOT 키가 있으면 명시적 사용 (R2 키 충돌 방지)."""
    root_key = os.getenv("AWS_ROOT_ACCESS_KEY_ID")
    root_secret = os.getenv("AWS_ROOT_SECRET_ACCESS_KEY")
    if root_key and root_secret:
        return boto3.client(
            service, region_name=REGION,
            aws_access_key_id=root_key, aws_secret_access_key=root_secret,
        )
    return boto3.client(service, region_name=REGION)


def start_ai_worker_instance():
    """
    API 서버에서 호출
    - ASG 내 AI 워커 인스턴스가 stopped이면 start
    - ASG desired=0이면 1로 올림
    - 이미 running이면 no-op (idempotent)
    """
    try:
        asg = _aws_client("autoscaling")
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

        if desired == 0:
            logger.info("[AI] ASG desired=0 → setting to 1")
            asg.set_desired_capacity(
                AutoScalingGroupName=AI_WORKER_ASG_NAME,
                DesiredCapacity=1,
            )
            return

        ec2 = _aws_client("ec2")
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


def scale_down_ai_worker_asg_to_zero_if_idle(queue_counts: dict[str, int]) -> bool:
    """
    AI worker self scale-in.

    CloudWatch SQS metrics can lag while a just-launched worker is still pulling
    its image. The worker therefore scales itself in only after live SQS counts
    prove that no visible, in-flight, or delayed messages remain.
    """
    visible = int(queue_counts.get("visible") or 0)
    not_visible = int(queue_counts.get("not_visible") or 0)
    delayed = int(queue_counts.get("delayed") or 0)
    if visible or not_visible or delayed:
        logger.info(
            "[AI] skip idle scale-in: visible=%d not_visible=%d delayed=%d",
            visible,
            not_visible,
            delayed,
        )
        return False

    try:
        asg = _aws_client("autoscaling")
        resp = asg.describe_auto_scaling_groups(
            AutoScalingGroupNames=[AI_WORKER_ASG_NAME]
        )
        groups = resp.get("AutoScalingGroups", [])
        if not groups:
            logger.warning("[AI] ASG %s not found — skip scale-in", AI_WORKER_ASG_NAME)
            return False

        desired = int(groups[0].get("DesiredCapacity") or 0)
        if desired <= 0:
            return True

        logger.info("[AI] idle queue confirmed → ASG desired=%d to 0", desired)
        asg.set_desired_capacity(
            AutoScalingGroupName=AI_WORKER_ASG_NAME,
            DesiredCapacity=0,
            HonorCooldown=False,
        )
        return True
    except Exception:
        logger.warning("[AI] AI 워커 idle scale-in 실패", exc_info=True)
        return False
