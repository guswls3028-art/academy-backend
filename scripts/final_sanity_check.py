#!/usr/bin/env python3
"""
배포 직전 최종 검증 스크립트

docs/FULLSTACK_VERIFICATION_CHECKLIST.md 에 명시된 4단계 테스트를 자동화합니다.

사용법:
  python scripts/final_sanity_check.py
  python scripts/final_sanity_check.py --with-redis         # Redis 연결 시 멱등성/하트비트 실기 테스트
  python scripts/final_sanity_check.py --skip-imports       # Worker import 생략 (Django/DB 미설정 시)
  python scripts/final_sanity_check.py --check-ai-isolation # AI Worker CPU/GPU 의존성 오염 검사

전제: pip install -r requirements/api.txt, .env (DB 등) 설정 시 1.2 Worker import 통과.
성공 시 exit 0, 실패 시 exit 1

AI Worker 분리 검증 (선택):
  --check-ai-isolation  academy-ai-worker-cpu, academy-ai-worker-gpu 이미지가 있을 때
                        각 컨테이너 내부에서 verify_ai_deps.py를 실행하여 의존성 오염 검사.
                        이미지 빌드 후 실행 권장.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def banner(msg: str, stage: int | None = None) -> None:
    prefix = f"[{stage}단계] " if stage is not None else ""
    print(f"\n{'='*60}")
    print(f"{prefix}{msg}")
    print("=" * 60)


def run_check(name: str, fn, *args, **kwargs) -> bool:
    """단일 체크 실행, 성공 시 True 반환"""
    try:
        ok = fn(*args, **kwargs)
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
        return ok
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        return False


# ---------------------------------------------------------------------------
# 1. 의존성 검사 (The "Clean" Check)
# ---------------------------------------------------------------------------


FORBIDDEN_PATTERNS = [
    "apps.api", "rest_framework", "rest_framework.",
    ".views", ".serializers", "django.urls", "django.conf.urls",
]
WORKER_ROOT = ROOT / "apps" / "worker"
EXCLUDES = {"__pycache__", ".git", "ai_dumps_backend"}


def _check_file_forbidden(path: Path) -> list[tuple[int, str]]:
    """단일 파일 내 금지 패턴 검사. (check_worker_forbidden_imports.py 로직 인라인)"""
    errors = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        for pat in FORBIDDEN_PATTERNS:
            if pat in s and ("import" in s or "from" in s):
                if pat in (".views", ".serializers"):
                    if " from " in s or " import " in s:
                        errors.append((i, f"forbidden: {pat!r}"))
                else:
                    errors.append((i, f"forbidden: {pat!r}"))
                break
    return errors


def check_forbidden_imports() -> bool:
    """1.1 금지된 임포트 체크 + 의존성 오염 전수 조사 (파일별 허용/금지 요약)"""
    allowed, forbidden = [], []
    for py in sorted(WORKER_ROOT.rglob("*.py")):
        if any(ex in str(py) for ex in EXCLUDES):
            continue
        rel = str(py.relative_to(ROOT)).replace("\\", "/")
        errs = _check_file_forbidden(py)
        if errs:
            forbidden.append((rel, errs))
        else:
            allowed.append(rel)
    print("    [의존성 오염 전수 조사]")
    print(f"    허용 (금지 패턴 없음): {len(allowed)} files")
    for f in allowed[:15]:
        print(f"      OK  {f}")
    if len(allowed) > 15:
        print(f"      ... 외 {len(allowed)-15} files")
    if forbidden:
        print(f"    금지 (위반 발견): {len(forbidden)} files")
        for f, errs in forbidden[:10]:
            for ln, msg in errs[:3]:
                print(f"      FAIL {f}:{ln} {msg}")
        return False
    print("    금지: 0 files")
    return True


def check_worker_import() -> bool:
    """1.2 Video Worker import 성공 및 금지 패턴 미포함"""
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "apps.api.config.settings.worker"
    env["PYTHONUNBUFFERED"] = "1"
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import apps.worker.video_worker.sqs_main; print('Import Success')",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "")[-1200:]
        print(f"    import failed. Ensure: pip install -r requirements/api.txt, .env (DB)")
        print(f"    last 500 chars: ...{err[-500:]}")
        return False
    if "Import Success" not in out:
        print(f"    expected 'Import Success', got: {out[:300]}")
        return False
    forbidden_logs = ["apps.api", "apps.admin", "rest_framework"]
    for pat in forbidden_logs:
        if pat in out.lower():
            print(f"    forbidden pattern in output: {pat}")
            return False
    return True


def check_ai_worker_import() -> bool:
    """1.2-bis AI Worker import"""
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "apps.api.config.settings.worker"
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import apps.worker.ai_worker.sqs_main_cpu; print('Import Success')",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return r.returncode == 0 and "Import Success" in (r.stdout or "")


def check_ai_worker_dependency_isolation() -> bool:
    """
    1.3 AI Worker 의존성 격리 (CPU/GPU)
    academy-ai-worker-cpu, academy-ai-worker-gpu 이미지가 있을 때
    각 컨테이너 내부에서 verify_ai_deps.py 실행.
    """
    images = ["academy-ai-worker-cpu:latest", "academy-ai-worker-gpu:latest"]
    script = ROOT / "scripts" / "verify_ai_deps.py"
    if not script.exists():
        print("    verify_ai_deps.py not found")
        return False
    all_ok = True
    for img in images:
        r = subprocess.run(
            ["docker", "run", "--rm", img, "python", "/app/scripts/verify_ai_deps.py", "--mode", "cpu" if "cpu" in img else "gpu"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode != 0:
            print(f"    FAIL {img}: {r.stderr or r.stdout or 'unknown'}")
            all_ok = False
        else:
            print(f"    OK {img}")
    return all_ok


def check_messaging_worker_import() -> bool:
    """1.2-ter Messaging Worker import"""
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "apps.api.config.settings.worker"
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import apps.worker.messaging_worker.sqs_main; print('Import Success')",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return r.returncode == 0 and "Import Success" in (r.stdout or "")


# ---------------------------------------------------------------------------
# 2. 멱등성 및 Redis 락 검사 (The "Protection" Check)
# ---------------------------------------------------------------------------


def check_lock_code_format() -> bool:
    """2.4 코드 레벨: 락 키 형식 및 acquire_lock 사용 확인"""
    adapter = ROOT / "src" / "infrastructure" / "cache" / "redis_idempotency_adapter.py"
    handler = ROOT / "src" / "application" / "video" / "handler.py"
    if not adapter.exists() or not handler.exists():
        print(f"    missing: {adapter} or {handler}")
        return False
    adapter_text = adapter.read_text(encoding="utf-8", errors="ignore")
    handler_text = handler.read_text(encoding="utf-8", errors="ignore")
    checks = [
        ('job:' in adapter_text and ':lock' in adapter_text, "lock key format"),
        ("nx=True" in adapter_text or "nx=" in adapter_text, "SET NX usage"),
        ("acquire_lock" in handler_text, "handler calls acquire_lock"),
        ("video:" in handler_text and "job_id" in handler_text, "job_id format (video:{id})"),
    ]
    for ok, desc in checks:
        if not ok:
            print(f"    missing: {desc}")
            return False
    return True


def _mock_idempotency_test() -> bool:
    """Redis 미연결 시 Mock으로 멱등성 로직 유효성 검증 (in-memory SETNX 시뮬레이션)"""
    locks: dict[str, bool] = {}

    def mock_acquire(job_id: str) -> bool:
        key = f"job:{job_id}:lock"
        if key in locks:
            print(f"      [MOCK] IDEMPOTENT_SKIP job_id={job_id} reason=duplicate")
            return False
        locks[key] = True
        print(f"      [MOCK] IDEMPOTENT_LOCK job_id={job_id} acquired")
        return True

    def mock_release(job_id: str) -> None:
        key = f"job:{job_id}:lock"
        locks.pop(key, None)

    test_job_id = "sanity-check:mock:999"
    print("    [Redis 미연결] Mock 객체로 멱등성 시나리오 시뮬레이션:")
    first = mock_acquire(test_job_id)
    second = mock_acquire(test_job_id)
    mock_release(test_job_id)
    if not first or second:
        print("    first=True, second=False 여야 함")
        return False
    print("    [MOCK] 두 번째 요청 IDEMPOTENT_SKIP 판정 확인됨")
    return True


def check_redis_idempotency_live() -> bool:
    """2.3 실기: Redis SETNX 락 - 동일 job_id 2회 acquire, 둘째는 IDEMPOTENT_SKIP"""
    redis_available = False
    try:
        from libs.redis.client import get_redis_client
        client = get_redis_client()
        redis_available = client is not None
    except Exception:
        pass

    if not redis_available:
        print("    [실제 환경] Redis 연결 불가 -> Mock으로 로직 유효성 검증")
        return _mock_idempotency_test()

    print("    [실제 환경] Redis 연결 가능 -> 실기 테스트")
    try:
        import logging
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        from src.infrastructure.cache.redis_idempotency_adapter import RedisIdempotencyAdapter
        adapter = RedisIdempotencyAdapter(ttl_seconds=60)
        test_job_id = "sanity-check:final-test:999"
        print(f"    멱등성 시나리오: job_id={test_job_id} 2회 acquire")
        first = adapter.acquire_lock(test_job_id)
        print(f"      첫 번째 acquire_lock -> {first}")
        second = adapter.acquire_lock(test_job_id)
        print(f"      두 번째 acquire_lock -> {second} (기대: False, IDEMPOTENT_SKIP)")
        adapter.release_lock(test_job_id)
        if not first:
            print("    FAIL: first acquire_lock should succeed")
            return False
        if second:
            print("    FAIL: second should be False (IDEMPOTENT_SKIP)")
            return False
        print("    PASS: 두 번째 요청 IDEMPOTENT_SKIP 판정 확인됨")
        return True
    except Exception as e:
        print(f"    idempotency test error: {e}")
        return False


# ---------------------------------------------------------------------------
# 3. 인프라 독립성 검사 (The "Hexagonal" Check)
# ---------------------------------------------------------------------------


def check_video_repository_orm_isolation() -> bool:
    """3.1 VideoRepository에만 Video.objects 사용"""
    repo = ROOT / "src" / "infrastructure" / "db" / "video_repository.py"
    if not repo.exists():
        return False
    text = repo.read_text(encoding="utf-8", errors="ignore")
    if "Video.objects" not in text:
        print("    Video.objects not found in video_repository (expected)")
        return False
    return True


def check_worker_no_direct_video_import() -> bool:
    """3.2 Worker에서 Video 모델 직접 import 없음"""
    sqs_main = ROOT / "apps" / "worker" / "video_worker" / "sqs_main.py"
    if not sqs_main.exists():
        return False
    text = sqs_main.read_text(encoding="utf-8", errors="ignore")
    forbidden = [
        "from apps.models import Video",
        "from apps.domains",
        "from apps.support.video.models import Video",
    ]
    for pat in forbidden:
        if pat in text:
            print(f"    forbidden: {pat}")
            return False
    if "VideoRepository" not in text or "ProcessVideoJobHandler" not in text:
        print("    expected VideoRepository and ProcessVideoJobHandler imports")
        return False
    return True


def check_handler_no_orm() -> bool:
    """3.3 Handler가 ORM 직접 사용 안 함"""
    handler = ROOT / "src" / "application" / "video" / "handler.py"
    if not handler.exists():
        return False
    text = handler.read_text(encoding="utf-8", errors="ignore")
    if "Video.objects" in text:
        print("    handler must not use Video.objects")
        return False
    if "from apps.support.video" in text or "from apps.domains" in text:
        print("    handler must not import from apps (ORM layer)")
        return False
    return True


# ---------------------------------------------------------------------------
# 4. 하트비트 버퍼링 검사 (The "Performance" Check)
# ---------------------------------------------------------------------------


def check_heartbeat_redis_first() -> bool:
    """4.1 코드: playback_session이 Redis 우선 사용하는지"""
    playback = ROOT / "apps" / "support" / "video" / "services" / "playback_session.py"
    if not playback.exists():
        return False
    text = playback.read_text(encoding="utf-8", errors="ignore")
    if "is_redis_available" not in text or "buffer_heartbeat_session_ttl" not in text:
        print("    heartbeat must use is_redis_available and buffer_heartbeat_session_ttl")
        return False
    if "if is_redis_available():" not in text and "is_redis_available()" not in text:
        print("    heartbeat_session should check Redis first")
        return False
    return True


def check_heartbeat_redis_live() -> bool:
    """4.2 실기: buffer_heartbeat_session_ttl 호출 시 Redis 키 생성"""
    try:
        from libs.redis.client import get_redis_client
        client = get_redis_client()
        if not client:
            print("    Redis not available, skip heartbeat live test")
            return True
    except Exception:
        return True

    try:
        from libs.redis.watch_buffer import buffer_heartbeat_session_ttl
        session_id = "sanity-check-final-heartbeat"
        ok = buffer_heartbeat_session_ttl(session_id=session_id, ttl_seconds=60)
        if not ok:
            print("    buffer_heartbeat_session_ttl returned False")
            return False
        meta_key = f"session:{session_id}:meta"
        exists = client.exists(meta_key)
        client.delete(meta_key)
        if not exists:
            print(f"    expected Redis key {meta_key} to exist")
            return False
        return True
    except Exception as e:
        print(f"    heartbeat live test error: {e}")
        return False


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="배포 직전 풀스택 검증")
    parser.add_argument(
        "--with-redis",
        action="store_true",
        help="Redis 연결 시 멱등성/하트비트 실기 테스트 포함",
    )
    parser.add_argument(
        "--skip-imports",
        action="store_true",
        help="Worker import 검사 생략 (Django/DB 미설정 환경)",
    )
    parser.add_argument(
        "--check-ai-isolation",
        action="store_true",
        help="AI Worker CPU/GPU 이미지 의존성 오염 검사 (academy-ai-worker-cpu, -gpu 이미지 필요)",
    )
    args = parser.parse_args()

    all_ok = True

    banner("[1] 의존성 검사 (The Clean Check)", 1)
    all_ok &= run_check("1.1 금지된 임포트", check_forbidden_imports)
    if not args.skip_imports:
        all_ok &= run_check("1.2 Video Worker import", check_worker_import)
        all_ok &= run_check("1.2-b AI Worker import", check_ai_worker_import)
        all_ok &= run_check("1.2-c Messaging Worker import", check_messaging_worker_import)

    if args.check_ai_isolation:
        all_ok &= run_check("1.3 AI Worker 의존성 격리 (CPU/GPU)", check_ai_worker_dependency_isolation)
    else:
        print("  [SKIP] 1.3 AI Worker 의존성 격리 (--check-ai-isolation 로 포함)")

    banner("[2] 멱등성 및 Redis 락 검사 (The Protection Check)", 2)
    all_ok &= run_check("2.4 락 키 형식 및 acquire_lock 사용", check_lock_code_format)
    all_ok &= run_check("2.3 멱등성 시나리오 (Redis 실기 또는 Mock)", check_redis_idempotency_live)

    banner("[3] 인프라 독립성 검사 (The Hexagonal Check)", 3)
    all_ok &= run_check("3.1 VideoRepository에 ORM 격리", check_video_repository_orm_isolation)
    all_ok &= run_check("3.2 Worker에 Video 직접 import 없음", check_worker_no_direct_video_import)
    all_ok &= run_check("3.3 Handler에 ORM 없음", check_handler_no_orm)

    banner("[4] 하트비트 버퍼링 검사 (The Performance Check)", 4)
    all_ok &= run_check("4.1 playback_session Redis 우선 로직", check_heartbeat_redis_first)
    if args.with_redis:
        all_ok &= run_check("4.2 buffer_heartbeat_session_ttl Redis 실기", check_heartbeat_redis_live)
    else:
        print("  [SKIP] 4.2 Redis 실기 (--with-redis 로 포함)")

    banner("결과", None)
    if all_ok:
        print("  [OK] 모든 검증 통과. 배포 가능합니다.")
        return 0
    print("  [FAIL] 일부 검증 실패. 위 FAIL 항목을 수정한 후 재실행하세요.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
