#!/usr/bin/env python
"""
Gate 10 — 7단계 런타임 테스트 스크립트

실행: (venv 활성화 후)
  set DJANGO_SETTINGS_MODULE=apps.api.config.settings.base
  python scripts/gate10_runtime_test.py [step]

step: migrate | ensure_user | ai_create | ai_status | notification_count
"""
from __future__ import annotations

import os
import sys
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.base")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
django.setup()

def step_ensure_user():
    from django.core.management import call_command
    call_command("ensure_dev_user", "--tenant=admin97", "--password=kjkszpj123", "--username=admin97")
    print("[Gate10] ensure_dev_user OK")

def step_ai_create():
    """AI Job 1건 생성 후 SQS enqueue (AWS 설정 시에만 성공)."""
    from apps.domains.ai.models import AIJobModel
    from apps.domains.ai.queueing.publisher import publish_ai_job_sqs
    from django.utils import timezone

    job_id = "gate10-test-" + timezone.now().strftime("%H%M%S")
    job, created = AIJobModel.objects.get_or_create(
        job_id=job_id,
        defaults={
            "job_type": "ocr",
            "status": "PENDING",
            "tier": "basic",
            "payload": {},
            "tenant_id": "1",
        },
    )
    if not created and job.status != "PENDING":
        print(f"[Gate10] Job exists: job_id={job_id}, status={job.status}")
        return job_id
    try:
        publish_ai_job_sqs(job)
        print(f"[Gate10] AI job enqueued: job_id={job_id}")
    except Exception as e:
        print(f"[Gate10] enqueue failed (AWS/SQS?): {e}")
    return job_id

def step_ai_status(job_id: str):
    """DB에서 AI job 상태 조회."""
    from apps.domains.ai.models import AIJobModel
    from django.db import connection
    with connection.cursor() as c:
        c.execute("SELECT job_id, status FROM ai_job WHERE job_id = %s", [job_id])
        row = c.fetchone()
    if row:
        print(f"[Gate10] DB ai_job: job_id={row[0]}, status={row[1]}")
        return row[1]
    print(f"[Gate10] DB ai_job: no row for job_id={job_id}")
    return None

def step_notification_count():
    """NotificationLog 테이블 레코드 수 (create_notification_log 검증용)."""
    from django.db import connection
    with connection.cursor() as c:
        c.execute("SELECT COUNT(*) FROM messaging_notificationlog")
        n = c.fetchone()[0]
    print(f"[Gate10] messaging_notificationlog count = {n}")
    return n

if __name__ == "__main__":
    step = (sys.argv[1] or "ensure_user").lower()
    if step == "ensure_user":
        step_ensure_user()
    elif step == "ai_create":
        step_ai_create()
    elif step == "ai_status":
        job_id = sys.argv[2] if len(sys.argv) > 2 else "gate10-test-000000"
        step_ai_status(job_id)
    elif step == "notification_count":
        step_notification_count()
    else:
        print("Usage: python gate10_runtime_test.py [ensure_user|ai_create|ai_status [job_id]|notification_count]")
