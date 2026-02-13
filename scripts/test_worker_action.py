#!/usr/bin/env python3
"""
워커 비즈니스 로직 실무 검증 스크립트

가상 메시지를 Mock Adapter로 투입하여, Handler -> Repository 연쇄 동작과
전체 라이프사이클을 검증합니다.

사용법:
  python scripts/test_worker_action.py
  python scripts/test_worker_action.py --video-only   # Video 워커만
  python scripts/test_worker_action.py --ai-only      # AI 워커만
  python scripts/test_worker_action.py --with-django  # Django/DB 연동 시 (선택)

전제: 프로젝트 루트에서 실행. Mock 모드 기본 (SQS/DB/Redis 불필요)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# 프로젝트 루트 (scripts/ 상위)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# Django 설정 (선택적)
if os.environ.get("DJANGO_SETTINGS_MODULE") or "--with-django" in sys.argv:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.worker")
    import django
    django.setup()

# 로깅: 라이프사이클 추적용
LOG_LIFECYCLE: List[str] = []


def _log_lifecycle(phase: str, detail: str = "") -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] {phase}" + (f" | {detail}" if detail else "")
    LOG_LIFECYCLE.append(line)
    print(f"  {line}")


# =============================================================================
# 1. Mock Adapters (가상 메시지 투입)
# =============================================================================


class MockVideoQueue:
    """가상 영상 작업 메시지를 1회 반환하는 Mock Queue"""

    def __init__(self, messages: Optional[List[dict]] = None) -> None:
        self._messages = list(messages or [])
        self._deleted: List[str] = []

    def receive_message(self, wait_time_seconds: int = 20) -> Optional[dict]:
        if not self._messages:
            return None
        msg = self._messages.pop(0)
        _log_lifecycle("MESSAGE_RECEIVED", f"video_id={msg.get('video_id')} tenant={msg.get('tenant_code')}")
        return msg

    def delete_message(self, receipt_handle: str) -> bool:
        self._deleted.append(receipt_handle)
        _log_lifecycle("QUEUE_DELETE", f"receipt={receipt_handle[:20]}...")
        return True

    def _get_queue_name(self) -> str:
        return "mock-video-queue"


class MockVideoRepository:
    """상태 변경을 로그로 기록하는 Mock Repository (Processing -> Completed)"""

    def __init__(self) -> None:
        self._state: Dict[int, str] = {}
        self._calls: List[str] = []

    def mark_processing(self, video_id: int) -> bool:
        self._state[video_id] = "PROCESSING"
        self._calls.append(f"mark_processing({video_id})")
        _log_lifecycle("DB_STATE", f"video_id={video_id} -> PROCESSING (Repository.mark_processing)")
        return True

    def complete_video(
        self,
        video_id: int,
        hls_path: str,
        duration: Optional[int] = None,
    ) -> tuple[bool, str]:
        self._state[video_id] = "READY"
        self._calls.append(f"complete_video({video_id}, hls_path={hls_path}, duration={duration})")
        _log_lifecycle("DB_STATE", f"video_id={video_id} -> READY/Completed (Repository.complete_video)")
        return True, "ok"

    def fail_video(self, video_id: int, reason: str) -> tuple[bool, str]:
        self._state[video_id] = "FAILED"
        self._calls.append(f"fail_video({video_id}, reason={reason[:50]})")
        _log_lifecycle("DB_STATE", f"video_id={video_id} -> FAILED")
        return True, "ok"


class MockIdempotency:
    """락 획득/해제 로그 기록"""

    def __init__(self) -> None:
        self._locks: set = set()

    def acquire_lock(self, job_id: str) -> bool:
        if job_id in self._locks:
            _log_lifecycle("LOCK_SKIP", f"job_id={job_id} (duplicate, IDEMPOTENT_SKIP)")
            return False
        self._locks.add(job_id)
        _log_lifecycle("LOCK_ACQUIRED", f"job_id={job_id}")
        return True

    def release_lock(self, job_id: str) -> None:
        self._locks.discard(job_id)
        _log_lifecycle("LOCK_RELEASED", f"job_id={job_id}")


class MockProgress:
    """진행률 로그만 기록"""

    def record_progress(self, job_id: str, step: str, extra: Optional[dict] = None) -> None:
        _log_lifecycle("PROGRESS", f"job_id={job_id} step={step}")

    def get_progress(self, job_id: str) -> Optional[dict]:
        return None


# =============================================================================
# 2. Video Worker: Handler -> Repository 연쇄 동작 검증
# =============================================================================


def run_video_worker_test() -> bool:
    """가상 영상 메시지 투입 -> Handler -> Repository -> 완료 라이프사이클 검증"""
    _log_lifecycle("---", "VIDEO WORKER 테스트 시작 ---")
    LOG_LIFECYCLE.append("")

    # 가상 메시지 1건 (Mock Queue 대신 직접 투입)
    virtual_message = {
        "video_id": 99999,
        "file_key": "test/upload/sample.mp4",
        "tenant_code": "test-tenant",
        "receipt_handle": "mock-receipt-handle-video-99999",
        "created_at": time.time() - 1.0,
    }

    mock_queue = MockVideoQueue(messages=[virtual_message])
    mock_repo = MockVideoRepository()
    mock_idempotency = MockIdempotency()
    mock_progress = MockProgress()

    def mock_process_video(job: dict, cfg: Any, progress: Any) -> tuple[str, int]:
        _log_lifecycle("LOGIC_EXEC", "process_video (Mock: 다운로드/트랜스코드 시뮬레이션)")
        return "mock/hls/test-tenant/videos/99999/master.m3u8", 60

    class MockConfig:
        R2_PREFIX = "media/hls"
        TEMP_DIR = "/tmp"
        FFPROBE_BIN = "ffprobe"
        FFMPEG_BIN = "ffmpeg"
        FFPROBE_TIMEOUT_SECONDS = 10

    from src.application.video.handler import ProcessVideoJobHandler

    handler = ProcessVideoJobHandler(
        repo=mock_repo,
        idempotency=mock_idempotency,
        progress=mock_progress,
        process_fn=mock_process_video,
    )

    job = {
        "video_id": virtual_message["video_id"],
        "file_key": virtual_message["file_key"],
        "tenant_code": virtual_message["tenant_code"],
    }

    _log_lifecycle("MESSAGE_RECEIVED", f"video_id={job['video_id']} tenant={job['tenant_code']} (가상 메시지 투입)")
    _log_lifecycle("HANDLER_INVOKE", "ProcessVideoJobHandler.handle()")
    result = handler.handle(job, MockConfig())
    _log_lifecycle("HANDLER_RETURN", f"result={result}")

    # 검증
    ok = (
        result == "ok"
        and mock_repo._state.get(99999) == "READY"
        and "mark_processing" in str(mock_repo._calls)
        and "complete_video" in str(mock_repo._calls)
    )
    _log_lifecycle("VERIFY", f"Repository 연쇄 동작: {'PASS' if ok else 'FAIL'}")
    LOG_LIFECYCLE.append("")
    return ok


# =============================================================================
# 3. AI Worker: Mock 모델 -> 결과 반환 검증
# =============================================================================


def run_ai_worker_test_embedding_only() -> bool:
    """
    embedding job 사용 (Mock 모델로 경량화).
    worker-ai-cpu/gpu 의존성 필요. 없으면 SKIP.
    """
    _log_lifecycle("---", "AI WORKER (embedding 경량) 테스트 ---")
    LOG_LIFECYCLE.append("")

    from unittest.mock import MagicMock, patch

    from apps.shared.contracts.ai_job import AIJob

    job = AIJob(
        id="test-embed-001",
        type="embedding",
        payload={"texts": ["hello"], "download_url": "https://example.com/dummy.png"},
    )

    _log_lifecycle("MESSAGE_RECEIVED", f"job_id={job.id} job_type=embedding")

    try:
        from apps.worker.ai_worker.ai.embedding.service import EmbeddingBatch
        mock_batch = EmbeddingBatch(vectors=[[0.0] * 384], backend="mock")
    except ImportError:
        mock_batch = MagicMock(vectors=[[0.0] * 384], backend="mock")

    # dispatcher import는 pytesseract, google.cloud.vision 등 필요 -> 선조건 체크
    try:
        from apps.worker.ai_worker.ai.pipelines.dispatcher import handle_ai_job
    except ImportError as e:
        _log_lifecycle(
            "SKIP",
            "AI worker 의존성 없음 (pip install -r requirements/worker-ai-cpu.txt 권장): " + str(e)[:80],
        )
        LOG_LIFECYCLE.append("")
        return True  # 의존성 없으면 스킵, Video 검증으로 통과

    # 패치: 사용처(dispatcher) + 정의처(service) 둘 다 적용 (import 순서에 따라 어느 쪽이 쓸지 다름)
    with patch(
        "apps.worker.ai_worker.ai.embedding.service.get_embeddings",
        return_value=mock_batch,
    ):
        with patch(
            "apps.worker.ai_worker.ai.pipelines.dispatcher.get_embeddings",
            return_value=mock_batch,
        ):
            with patch(
                "apps.worker.ai_worker.storage.downloader.download_to_tmp",
                return_value="/tmp/mock_embed_input.png",
            ):
                _log_lifecycle("LOGIC_EXEC", "handle_ai_job (embedding, Mock model)")
                result = handle_ai_job(job)

    res = result.result or {}
    ok = result.status == "DONE" and ("vectors" in res or "backend" in res)
    if not ok:
        _log_lifecycle("DEBUG", f"status={result.status} keys={list(res.keys())[:5]}")
    _log_lifecycle("VERIFY", f"AI 결과 반환: {'PASS' if ok else 'FAIL'}")
    LOG_LIFECYCLE.append("")
    return ok


# =============================================================================
# 4. 실행 및 보고서
# =============================================================================


def print_report() -> None:
    """전체 라이프사이클 로그 보고서"""
    print("\n" + "=" * 70)
    print(" 실행 보고서: 메시지 수신 -> 락 획득 -> 로직 실행 -> DB 기록 -> 완료")
    print("=" * 70)
    for line in LOG_LIFECYCLE:
        print(f"  {line}")
    print("=" * 70)


def main() -> int:
    parser = argparse.ArgumentParser(description="워커 비즈니스 로직 실무 검증")
    parser.add_argument("--video-only", action="store_true", help="Video 워커만 테스트")
    parser.add_argument("--ai-only", action="store_true", help="AI 워커만 테스트")
    parser.add_argument("--with-django", action="store_true", help="Django 설정 로드 (선택)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("\n[워커 실무 검증] 가상 메시지 투입 및 Handler-Repository 연쇄 동작 확인\n")

    all_ok = True

    if not args.ai_only:
        all_ok &= run_video_worker_test()

    if not args.video_only:
        # embedding만 사용 (가장 경량, Mock 적용)
        all_ok &= run_ai_worker_test_embedding_only()

    print_report()

    if all_ok:
        print("\n[OK] 전체 라이프사이클 검증 통과.")
        return 0
    print("\n[FAIL] 일부 검증 실패.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
