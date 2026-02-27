#!/usr/bin/env python
"""
AWS SQS 리소스 생성 스크립트 - AI Worker용

AI Worker용 SQS 큐 및 DLQ 생성 (CPU/GPU 분리)
"""

import boto3
import json
from botocore.exceptions import ClientError


def create_ai_sqs_resources(region_name: str = "ap-northeast-2"):
    """
    AI Worker용 SQS 리소스 생성 (3-Tier 시스템)
    
    생성 리소스:
    1. academy-ai-jobs-lite (Lite 큐)
    2. academy-ai-jobs-basic (Basic 큐)
    3. academy-ai-jobs-premium (Premium 큐, 향후 GPU)
    4. 각 큐별 DLQ
    """
    sqs = boto3.client("sqs", region_name=region_name)
    
    queues_config = [
        {
            "name": "academy-ai-jobs-lite",
            "dlq_name": "academy-ai-jobs-lite-dlq",
            "visibility_timeout": "3600",  # 60분 (lease 3540, safety_margin 60)
            "tier": "lite",
        },
        {
            "name": "academy-ai-jobs-basic",
            "dlq_name": "academy-ai-jobs-basic-dlq",
            "visibility_timeout": "3600",  # 60분
            "tier": "basic",
        },
        {
            "name": "academy-ai-jobs-premium",
            "dlq_name": "academy-ai-jobs-premium-dlq",
            "visibility_timeout": "3600",  # 60분
            "tier": "premium",
        },
    ]
    
    created_queues = {}
    
    for queue_config in queues_config:
        queue_name = queue_config["name"]
        dlq_name = queue_config["dlq_name"]
        visibility_timeout = queue_config["visibility_timeout"]
        tier = queue_config["tier"]
        
        # DLQ 생성
        dlq_arn = None
        try:
            dlq_response = sqs.create_queue(
                QueueName=dlq_name,
                Attributes={
                    "MessageRetentionPeriod": "1209600",  # 14일
                },
            )
            dlq_url = dlq_response["QueueUrl"]
            dlq_attributes = sqs.get_queue_attributes(
                QueueUrl=dlq_url,
                AttributeNames=["QueueArn"],
            )
            dlq_arn = dlq_attributes["Attributes"]["QueueArn"]
            print(f"✅ DLQ 생성 완료: {dlq_name} (tier={tier})")
        except ClientError as e:
            if e.response["Error"]["Code"] == "QueueAlreadyExists":
                print(f"⚠️  DLQ가 이미 존재함: {dlq_name}")
                dlq_url = sqs.get_queue_url(QueueName=dlq_name)["QueueUrl"]
                dlq_attributes = sqs.get_queue_attributes(
                    QueueUrl=dlq_url,
                    AttributeNames=["QueueArn"],
                )
                dlq_arn = dlq_attributes["Attributes"]["QueueArn"]
            else:
                raise
        
        # 메인 큐 생성
        try:
            queue_response = sqs.create_queue(
                QueueName=queue_name,
                Attributes={
                    "VisibilityTimeout": visibility_timeout,
                    "MessageRetentionPeriod": "1209600",  # 14일
                    "ReceiveMessageWaitTimeSeconds": "20",  # Long Polling
                    "RedrivePolicy": json.dumps({
                        "deadLetterTargetArn": dlq_arn,
                        "maxReceiveCount": "3",  # 3회 재시도 후 DLQ로 이동
                    }),
                },
            )
            queue_url = queue_response["QueueUrl"]
            print(f"✅ {tier.upper()} 큐 생성 완료: {queue_name}")
            print(f"   URL: {queue_url}")
            print(f"   DLQ 연결: {dlq_name}")
            created_queues[tier] = queue_name
        except ClientError as e:
            if e.response["Error"]["Code"] == "QueueAlreadyExists":
                print(f"⚠️  {tier.upper()} 큐가 이미 존재함: {queue_name}")
                queue_url = sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
                created_queues[tier] = queue_name
            else:
                raise
    
    print("\n✅ AI Worker SQS 리소스 생성 완료 (3-Tier 시스템)!")
    print(f"\n환경변수 설정:")
    print(f"  AI_SQS_QUEUE_NAME_LITE={created_queues.get('lite')}")
    print(f"  AI_SQS_QUEUE_NAME_BASIC={created_queues.get('basic')}")
    print(f"  AI_SQS_QUEUE_NAME_PREMIUM={created_queues.get('premium')}")
    print(f"  AI_SQS_DLQ_NAME_LITE=academy-ai-jobs-lite-dlq")
    print(f"  AI_SQS_DLQ_NAME_BASIC=academy-ai-jobs-basic-dlq")
    print(f"  AI_SQS_DLQ_NAME_PREMIUM=academy-ai-jobs-premium-dlq")
    print(f"  AWS_REGION={region_name}")
    print(f"  QUEUE_BACKEND=sqs")


if __name__ == "__main__":
    import sys
    
    region = sys.argv[1] if len(sys.argv) > 1 else "ap-northeast-2"
    create_ai_sqs_resources(region_name=region)
