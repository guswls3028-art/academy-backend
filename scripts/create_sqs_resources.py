#!/usr/bin/env python
"""
AWS SQS 리소스 생성 스크립트

Video Worker용 SQS 큐 및 DLQ 생성
"""

import boto3
import json
from botocore.exceptions import ClientError


def create_sqs_resources(region_name: str = "ap-northeast-2"):
    """
    Video Worker용 SQS 리소스 생성
    
    생성 리소스:
    1. academy-video-jobs (메인 큐)
    2. academy-video-jobs-dlq (Dead Letter Queue)
    """
    sqs = boto3.client("sqs", region_name=region_name)
    
    # 1. DLQ 생성
    dlq_name = "academy-video-jobs-dlq"
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
        print(f"✅ DLQ 생성 완료: {dlq_name}")
        print(f"   URL: {dlq_url}")
        print(f"   ARN: {dlq_arn}")
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
    
    # 2. 메인 큐 생성 (DLQ 연결)
    queue_name = "academy-video-jobs"
    
    # VisibilityTimeout: 6h (21600). Must be >= ffmpeg timeout (max 6h = duration*1.5 cap).
    # 기존 큐가 이미 있으면 AWS 콘솔에서 VisibilityTimeout = 21600 으로 변경 필요.
    try:
        queue_response = sqs.create_queue(
            QueueName=queue_name,
            Attributes={
                "VisibilityTimeout": "21600",  # 6h. Worker extends to same at job start.
                "MessageRetentionPeriod": "1209600",  # 14일
                "ReceiveMessageWaitTimeSeconds": "20",  # Long Polling
                "RedrivePolicy": json.dumps({
                    "deadLetterTargetArn": dlq_arn,
                    "maxReceiveCount": "3",  # 3회 재시도 후 DLQ로 이동
                }),
            },
        )
        queue_url = queue_response["QueueUrl"]
        print(f"✅ 메인 큐 생성 완료: {queue_name}")
        print(f"   URL: {queue_url}")
        print(f"   DLQ 연결: {dlq_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "QueueAlreadyExists":
            print(f"⚠️  메인 큐가 이미 존재함: {queue_name}")
            queue_url = sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
        else:
            raise
    
    print("\n✅ Video SQS 리소스 생성 완료!")
    print("   VisibilityTimeout=21600(6h) — SQS visibility >= ffmpeg timeout (max 6h).")
    print("   기존 큐가 이미 있으면: AWS SQS 콘솔에서 해당 큐 → Edit → Visibility timeout = 21600 으로 변경.")
    print(f"\n환경변수 설정:")
    print(f"  VIDEO_SQS_QUEUE_NAME={queue_name}")
    print(f"  VIDEO_SQS_DLQ_NAME={dlq_name}")
    print(f"  AWS_REGION={region_name}")
    print(f"  QUEUE_BACKEND=sqs")


def create_messaging_sqs_resources(region_name: str = "ap-northeast-2"):
    """
    Messaging Worker용 SQS 큐 및 DLQ 생성

    생성 리소스:
    1. academy-messaging-jobs (메인 큐)
    2. academy-messaging-jobs-dlq (Dead Letter Queue)
    """
    sqs = boto3.client("sqs", region_name=region_name)
    dlq_name = "academy-messaging-jobs-dlq"
    queue_name = "academy-messaging-jobs"
    dlq_arn = None

    try:
        dlq_response = sqs.create_queue(
            QueueName=dlq_name,
            Attributes={"MessageRetentionPeriod": "1209600"},
        )
        dlq_url = dlq_response["QueueUrl"]
        dlq_attributes = sqs.get_queue_attributes(
            QueueUrl=dlq_url, AttributeNames=["QueueArn"]
        )
        dlq_arn = dlq_attributes["Attributes"]["QueueArn"]
        print(f"✅ Messaging DLQ 생성 완료: {dlq_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "QueueAlreadyExists":
            print(f"⚠️  Messaging DLQ가 이미 존재함: {dlq_name}")
            dlq_url = sqs.get_queue_url(QueueName=dlq_name)["QueueUrl"]
            dlq_attributes = sqs.get_queue_attributes(
                QueueUrl=dlq_url, AttributeNames=["QueueArn"]
            )
            dlq_arn = dlq_attributes["Attributes"]["QueueArn"]
        else:
            raise

    # VisibilityTimeout: 솔라피 타임아웃(약 5~10초)보다 넉넉히 30~60초 (중복 발송 방지)
    # DLQ: 3번 재시도 후 실패 메시지 격리 → "왜 문자 안 왔냐" 민원 확인용 로그
    # Long Polling: WaitTimeSeconds 20초 → SQS 빈 쿼리 비용 최소화
    try:
        queue_response = sqs.create_queue(
            QueueName=queue_name,
            Attributes={
                "VisibilityTimeout": "60",  # 30~60초 권장 (Solapi 5~10초 대비 여유)
                "MessageRetentionPeriod": "1209600",
                "ReceiveMessageWaitTimeSeconds": "20",
                "RedrivePolicy": json.dumps({
                    "deadLetterTargetArn": dlq_arn,
                    "maxReceiveCount": "3",  # 3회 재시도 후 DLQ로 격리
                }),
            },
        )
        print(f"✅ Messaging 메인 큐 생성 완료: {queue_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "QueueAlreadyExists":
            print(f"⚠️  Messaging 메인 큐가 이미 존재함: {queue_name}")
        else:
            raise

    print(f"\n환경변수: MESSAGING_SQS_QUEUE_NAME={queue_name}")


if __name__ == "__main__":
    import sys

    region = sys.argv[1] if len(sys.argv) > 1 else "ap-northeast-2"
    create_sqs_resources(region_name=region)
    print()
    create_messaging_sqs_resources(region_name=region)
