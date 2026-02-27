#!/usr/bin/env python
"""
SQS Messaging Worker 스트레스 테스트

- academy-messaging-jobs 에 메시지 100건 enqueue
- (선택) 워커를 띄우고 큐가 비워질 때까지 대기
- 순서/에러 시 재진입 여부 확인용. DEBUG=True 로 워커를 돌리면 실제 API 호출 없이 Mock으로 처리됨.

사용법:
  # 100건만 넣고, 워커는 별도 터미널에서 실행
  python scripts/stress_test_messaging_worker.py

  # 100건 넣은 뒤 워커를 자동 실행하고 큐가 0이 될 때까지 대기
  python scripts/stress_test_messaging_worker.py --run-worker
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

# Django 설정 (enqueue 시 MessagingSQSQueue 사용)
if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.worker")

def main():
    parser = argparse.ArgumentParser(description="Messaging Worker stress test: enqueue 100, optionally run worker")
    parser.add_argument("--run-worker", action="store_true", help="Enqueue 후 워커를 subprocess로 실행하고 큐가 0이 될 때까지 대기")
    parser.add_argument("--count", type=int, default=100, help="Enqueue 할 메시지 수 (기본 100)")
    parser.add_argument("--timeout", type=int, default=120, help="큐가 비워질 때까지 대기 시간(초, 기본 120)")
    args = parser.parse_args()

    import django
    django.setup()
    from django.conf import settings
    from apps.support.messaging.sqs_queue import MessagingSQSQueue

    queue_name = getattr(settings, "MESSAGING_SQS_QUEUE_NAME", "academy-messaging-jobs")
    queue = MessagingSQSQueue()
    count = max(1, min(args.count, 1000))

    # tenant_id: 스트레스 테스트용 1 (실제 발송 시 워커가 해당 테넌트 잔액/발신번호 사용)
    tenant_id = getattr(settings, "STRESS_TEST_TENANT_ID", 1)
    print(f"[stress] Enqueuing {count} messages to {queue_name} (tenant_id={tenant_id})...")
    ok = 0
    for i in range(count):
        if queue.enqueue(
            tenant_id=tenant_id,
            to="01000000000",  # 테스트용 더미 번호
            text=f"stress-test-{i+1}/{count}",
            sender=getattr(settings, "SOLAPI_SENDER", "01012345678") or "01012345678",
        ):
            ok += 1
    print(f"[stress] Enqueued {ok}/{count} messages.")

    if ok == 0:
        print("[stress] No messages enqueued. Check queue/SQS config.")
        return 1

    # 큐 메시지 수 조회 (boto3)
    def get_queue_depth():
        try:
            import boto3
            region = os.environ.get("AWS_REGION", "ap-northeast-2")
            sqs = boto3.client("sqs", region_name=region)
            resp = sqs.get_queue_url(QueueName=queue_name)
            url = resp["QueueUrl"]
            attr = sqs.get_queue_attributes(
                QueueUrl=url,
                AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
            )
            visible = int(attr["Attributes"].get("ApproximateNumberOfMessages", 0))
            in_flight = int(attr["Attributes"].get("ApproximateNumberOfMessagesNotVisible", 0))
            return visible, in_flight
        except Exception as e:
            print(f"[stress] get_queue_depth error: {e}")
            return None, None

    visible, in_flight = get_queue_depth()
    if visible is not None:
        print(f"[stress] Queue depth now: visible={visible}, in_flight={in_flight}")

    if not args.run_worker:
        print("[stress] Run worker in another terminal to process messages:")
        print("  DEBUG=True python -m apps.worker.messaging_worker.sqs_main")
        print("[stress] Or run this script with --run-worker to run worker automatically.")
        return 0

    print("[stress] Starting worker (DEBUG=True, MockSolapi)...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "apps.worker.messaging_worker.sqs_main"],
        env={**os.environ, "DEBUG": "true"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            visible, in_flight = get_queue_depth()
            if visible is not None and visible == 0 and in_flight == 0:
                print(f"[stress] Queue empty. Worker processed all messages.")
                break
            if visible is not None:
                print(f"[stress] Waiting for queue to drain... visible={visible}, in_flight={in_flight}")
            time.sleep(2)
        else:
            print(f"[stress] Timeout ({args.timeout}s). Queue may still have messages.")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
