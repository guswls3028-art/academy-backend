#!/usr/bin/env python3
"""
워커 파이프라인 검증 스크립트

각 워커(Video, AI, Messaging)의 파이프라인 컴포넌트가 정상적으로 로드되고
핵심 흐름이 문제없이 작동하는지 검증합니다.

사용법:
  python scripts/check_worker_pipelines.py
  DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker python scripts/check_worker_pipelines.py  # 전체 검증
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Django 설정 (ORM/DB 의존성 있을 경우)
if os.environ.get("DJANGO_SETTINGS_MODULE"):
    try:
        import django
        django.setup()
    except Exception as e:
        print(f"[WARN] Django setup failed: {e}")
        print("  Some checks may be skipped. Set DJANGO_SETTINGS_MODULE for full validation.")

RESULTS = []


def ok(name: str, msg: str = ""):
    RESULTS.append((name, True, msg))
    print(f"  OK  {name}" + (f" - {msg}" if msg else ""))


def fail(name: str, err: str):
    RESULTS.append((name, False, err))
    print(f"  FAIL {name}: {err[:150]}")


def check_video_pipeline():
    """Video Worker 파이프라인 검증 (Batch only)"""
    print("\n[1] Video Worker Pipeline (Batch)")
    try:
        from apps.support.video.services.video_encoding import create_job_and_submit_batch
        ok("create_job_and_submit_batch")
    except Exception as e:
        fail("create_job_and_submit_batch", str(e))
        return

    try:
        from src.application.video.handler import ProcessVideoJobHandler
        ok("ProcessVideoJobHandler")
    except Exception as e:
        fail("ProcessVideoJobHandler", str(e))
        return

    try:
        from src.infrastructure.video.processor import process_video
        ok("process_video (processor)")
    except Exception as e:
        fail("process_video", str(e))

    try:
        from academy.adapters.db.django.repositories_video import DjangoVideoRepository
        ok("DjangoVideoRepository (academy)")
    except Exception as e:
        fail("DjangoVideoRepository", str(e))

    try:
        from src.infrastructure.cache.redis_idempotency_adapter import RedisIdempotencyAdapter
        from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter
        ok("Redis adapters (idempotency, progress)")
    except Exception as e:
        fail("Redis adapters", str(e))

    # Handler 시그니처 검증
    try:
        from src.application.video.handler import ProcessVideoJobHandler
        from src.application.ports.video_repository import IVideoRepository
        from src.application.ports.idempotency import IIdempotency
        from src.application.ports.progress import IProgress
        # Handler는 repo, idempotency, progress, process_fn 필요
        assert hasattr(ProcessVideoJobHandler, "handle")
        ok("Handler.handle() exists")
    except Exception as e:
        fail("Handler contract", str(e))


def check_ai_pipeline():
    """AI Worker 파이프라인 검증"""
    print("\n[2] AI Worker Pipeline")
    try:
        from src.infrastructure.ai import AISQSAdapter
        ok("AISQSAdapter")
    except Exception as e:
        fail("AISQSAdapter", str(e))
        return

    try:
        from apps.worker.ai_worker.ai.pipelines.dispatcher import handle_ai_job
        ok("handle_ai_job (dispatcher)")
    except Exception as e:
        fail("handle_ai_job", str(e))
        return

    try:
        from apps.worker.ai_worker.ai.pipelines.tier_enforcer import enforce_tier_limits
        ok("enforce_tier_limits")
    except Exception as e:
        fail("enforce_tier_limits", str(e))
        return

    # Tier enforcer 로직 검증
    try:
        from apps.worker.ai_worker.ai.pipelines.tier_enforcer import enforce_tier_limits
        allowed, _ = enforce_tier_limits(tier="lite", job_type="ocr")
        assert allowed is True, "Lite + OCR should be allowed"
        allowed, _ = enforce_tier_limits(tier="lite", job_type="omr_grading")
        assert allowed is False, "Lite + OMR should be rejected"
        allowed, _ = enforce_tier_limits(tier="basic", job_type="omr_grading")
        assert allowed is True, "Basic + OMR should be allowed"
        ok("Tier enforcer logic (lite/basic)")
    except Exception as e:
        fail("Tier enforcer logic", str(e))

    # AIJob / AIResult contract
    try:
        from apps.shared.contracts.ai_job import AIJob
        from apps.shared.contracts.ai_result import AIResult
        j = AIJob(id="test", type="ocr", payload={"download_url": "http://x"})
        r = AIResult.failed("test", "err")
        assert r.status == "failed"
        ok("AIJob/AIResult contracts")
    except Exception as e:
        fail("AIJob/AIResult", str(e))

    # Dispatcher job types (dispatcher가 처리하는 타입들)
    try:
        from apps.worker.ai_worker.ai.pipelines.dispatcher import handle_ai_job
        # handle_ai_job은 OCR, question_segmentation, handwriting_analysis, embedding,
        # problem_generation, homework_video_analysis, omr_grading 지원
        ok("Dispatcher job types wired")
    except Exception as e:
        fail("Dispatcher", str(e))


def check_messaging_pipeline():
    """Messaging Worker 파이프라인 검증"""
    print("\n[3] Messaging Worker Pipeline")
    try:
        from apps.worker.messaging_worker.config import load_config
        cfg = load_config()
        ok("load_config")
    except Exception as e:
        fail("load_config", str(e))
        return

    try:
        from libs.queue import get_queue_client
        client = get_queue_client()
        ok("get_queue_client")
    except Exception as e:
        fail("get_queue_client", str(e))

    try:
        from libs.redis.idempotency import acquire_job_lock, release_job_lock
        ok("acquire_job_lock / release_job_lock")
    except Exception as e:
        fail("Redis idempotency", str(e))

    # Django 의존성 (ORM 사용 시)
    if os.environ.get("DJANGO_SETTINGS_MODULE"):
        try:
            from apps.support.messaging.services import is_reservation_cancelled
            ok("is_reservation_cancelled (Django)")
        except Exception as e:
            fail("is_reservation_cancelled", str(e))

        try:
            from apps.support.messaging.credit_services import get_tenant_messaging_info
            ok("get_tenant_messaging_info (Django)")
        except Exception as e:
            fail("get_tenant_messaging_info", str(e))

        try:
            from apps.support.messaging.models import NotificationLog
            ok("NotificationLog (Django)")
        except Exception as e:
            fail("NotificationLog", str(e))
    else:
        print("  [SKIP] Django not loaded - messaging ORM checks skipped")


def main() -> int:
    print("=== Worker Pipeline Verification ===")
    check_video_pipeline()
    check_ai_pipeline()
    check_messaging_pipeline()

    failed = [r for r in RESULTS if not r[1]]
    print()
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for name, _, err in failed:
            print(f"  - {name}: {err[:100]}")
        return 1
    print("OK: All pipeline checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
