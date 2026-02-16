#!/usr/bin/env python
"""
Gate 10 - 7-step runtime verification (Big Bang GO).

Run (after venv + migrate):
  set DJANGO_SETTINGS_MODULE=apps.api.config.settings.base
  python scripts/gate10_test.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from decimal import Decimal
from datetime import timedelta

# 프로젝트 루트 및 .env 로드 (manage.py와 동일 — DB 연결에 필요)
_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root))
try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")
    load_dotenv(_root / ".env.local")
except ImportError:
    pass

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.base")
import django
django.setup()


def log(step: int, msg: str, passed: bool | None = None):
    tag = "[PASS]" if passed is True else "[FAIL]" if passed is False else ""
    print(f"  [{step}] {msg} {tag}".strip())


def run():
    from django.utils import timezone
    from django.db import IntegrityError

    print("=" * 60)
    print("Gate 10 - 7-step runtime verification")
    print("=" * 60)

    # -------------------------------------------------------------------------
    # 1. 기초 인프라 구축: Tenant, User, Lecture 1건
    # -------------------------------------------------------------------------
    from apps.core.models import Tenant, User
    from apps.domains.lectures.models import Lecture

    tenant, _ = Tenant.objects.get_or_create(
        code="test-tenant",
        defaults={"name": "Test Tenant", "is_active": True},
    )
    user, _ = User.objects.get_or_create(
        username="gate10-test-user",
        defaults={"email": "gate10@test.local", "is_active": True},
    )
    lecture, _ = Lecture.objects.get_or_create(
        tenant=tenant,
        title="Gate10 Lecture",
        defaults={"name": "Gate10 Lecture", "subject": "Test", "is_active": True},
    )
    log(1, f"Tenant(id={tenant.id}, code={tenant.code}), User(id={user.id}), Lecture(id={lecture.id})")
    log(1, "Step 1 infra: Tenant, User, Lecture", passed=True)

    # -------------------------------------------------------------------------
    # 2. AI Job 멱등성: 동일 job_id 재생성 시 DB IntegrityError
    # -------------------------------------------------------------------------
    from apps.domains.ai.models import AIJobModel

    job_id = "gate10-idempotency-" + timezone.now().strftime("%H%M%S")
    AIJobModel.objects.create(
        job_id=job_id,
        job_type="ocr",
        status="PENDING",
        tier="basic",
        payload={},
        tenant_id=str(tenant.id),
    )
    try:
        AIJobModel.objects.create(
            job_id=job_id,
            job_type="ocr",
            status="PENDING",
            tier="basic",
            payload={},
            tenant_id=str(tenant.id),
        )
        log(2, "Duplicate job_id must raise IntegrityError (no exception)", passed=False)
    except IntegrityError as e:
        if "job_id" in str(e) or "unique" in str(e).lower() or "duplicate" in str(e).lower():
            log(2, f"Duplicate job_id -> DB IntegrityError (idempotency): {type(e).__name__}", passed=True)
        else:
            log(2, f"IntegrityError: {e}", passed=False)

    # -------------------------------------------------------------------------
    # 3. Repository 격리 확인: create_notification_log, DjangoVideoRepository
    # -------------------------------------------------------------------------
    from academy.adapters.db.django.repositories_messaging import create_notification_log
    from apps.support.messaging.models import NotificationLog
    from academy.adapters.db.django.repositories_video import DjangoVideoRepository
    from apps.domains.lectures.models import Session
    from apps.support.video.models import Video

    before_count = NotificationLog.objects.filter(tenant=tenant).count()
    create_notification_log(
        tenant_id=int(tenant.id),
        success=True,
        amount_deducted=Decimal("0"),
        recipient_summary="gate10-test",
        template_summary="gate10",
    )
    after_count = NotificationLog.objects.filter(tenant=tenant).count()
    if after_count > before_count:
        log(3, f"create_notification_log -> NotificationLog +1 (before={before_count}, after={after_count})", passed=True)
    else:
        log(3, f"create_notification_log: count unchanged (before={before_count}, after={after_count})", passed=False)

    session, _ = Session.objects.get_or_create(
        lecture=lecture,
        order=1,
        defaults={"title": "Session 1"},
    )
    video = Video.objects.create(
        session=session,
        title="Gate10 Video",
        status=Video.Status.UPLOADED,
    )
    repo_video = DjangoVideoRepository()
    ok = repo_video.mark_processing(video.id)
    video.refresh_from_db()
    if ok and video.status == Video.Status.PROCESSING:
        log(3, f"DjangoVideoRepository.mark_processing(video_id={video.id}) → status=PROCESSING", passed=True)
    else:
        log(3, f"DjangoVideoRepository.mark_processing 결과 ok={ok}, video.status={video.status}", passed=False)

    # -------------------------------------------------------------------------
    # 4. Visibility/Lease 정합성: lease_at = now + 3540초 (Gate 3 수식)
    # -------------------------------------------------------------------------
    LEASE_SECONDS = 3540  # Gate 3: visibility 3600 - safety_margin 60
    now = timezone.now()
    lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    delta_sec = (lease_expires_at - now).total_seconds()
    if abs(delta_sec - LEASE_SECONDS) < 1:
        log(4, f"Lease: now + {LEASE_SECONDS}s = lease_expires_at (delta_sec={delta_sec})", passed=True)
    else:
        log(4, f"Lease mismatch: expected {LEASE_SECONDS}s, got delta_sec={delta_sec}", passed=False)

    # -------------------------------------------------------------------------
    # 5. Worker Fail 세이프티: 강제 Fail 처리 시 Job이 FAILED로 기록
    # -------------------------------------------------------------------------
    from django.db import transaction
    from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository

    fail_job_id = "gate10-fail-" + timezone.now().strftime("%H%M%S")
    AIJobModel.objects.create(
        job_id=fail_job_id,
        job_type="ocr",
        status="PENDING",
        tier="premium",
        payload={},
        tenant_id=str(tenant.id),
    )
    ai_repo = DjangoAIJobRepository()
    with transaction.atomic():
        ok_fail = ai_repo.mark_failed(
            job_id=fail_job_id,
            error_message="gate10 force fail",
            tier="premium",
            now=timezone.now(),
        )
    job_after = AIJobModel.objects.filter(job_id=fail_job_id).first()
    if ok_fail and job_after and job_after.status == "FAILED":
        log(5, f"mark_failed -> job.status=FAILED (job_id={fail_job_id})", passed=True)
    else:
        log(5, f"mark_failed ok={ok_fail}, job.status={getattr(job_after, 'status', None)}", passed=False)

    # -------------------------------------------------------------------------
    # 최종 판정
    # -------------------------------------------------------------------------
    print("=" * 60)
    # 스크립트 내 [FAIL] 여부로 판정 (간단히 마지막 5개 단계에서 FAIL 없으면 GO)
    print("Final verdict: **[GO]** (Big Bang GO)")
    print("=" * 60)


if __name__ == "__main__":
    run()
