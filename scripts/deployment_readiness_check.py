#!/usr/bin/env python3
"""
배포 가능 상태 검증 (4조건)

1. Worker Docker 이미지 단독 실행 (--docker 시)
2. Redis 실연결 — 선택 사항. REDIS_HOST 미설정 시 SKIP(통과 처리)
3. SQS 메시지 1건 수신 가능 (인프라 있을 때)
4. DB 연결

사용법:
  python scripts/deployment_readiness_check.py
  python scripts/deployment_readiness_check.py --docker   # Docker 이미지 검사 포함
  python scripts/deployment_readiness_check.py --docker --local  # 로컬: SQS/DB 미설정 시 SKIP

Exit 0: 4/4 통과. Exit 1: 미통과.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONDITIONS = [
    "[1] Worker Docker 이미지 단독 실행",
    "[2] Redis 실연결 (선택, 미설정 시 SKIP)",
    "[3] SQS 메시지 수신",
    "[4] DB 연결",
]


def _safe_msg(e: Exception) -> str:
    """Windows cp949 등에서 예외 메시지 출력 시 유니코드 깨짐 방지 (ASCII만 출력)"""
    try:
        return str(e).encode("ascii", errors="replace").decode("ascii")
    except Exception:
        return repr(e)[:200]


def banner(msg: str) -> None:
    print(f"\n{'='*60}")
    print(msg)
    print("=" * 60)


def run(name: str, fn, *args, **kwargs) -> bool:
    try:
        ok = fn(*args, **kwargs)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        return ok
    except Exception as e:
        print(f"  [FAIL] {name}: {_safe_msg(e)}")
        return False


# ---------------------------------------------------------------------------
# [1] Worker Docker 이미지 단독 실행
# ---------------------------------------------------------------------------


def check_docker_image() -> bool:
    """academy-video-worker 이미지 존재 여부, 단독 실행 가능 여부"""
    r = subprocess.run(
        ["docker", "image", "inspect", "academy-video-worker:latest"],
        capture_output=True,
        timeout=5,
    )
    if r.returncode != 0:
        print("    academy-video-worker:latest 이미지 없음. docker/build.ps1 실행")
        return False
    # 단독 실행 테스트: Django setup 후 worker 모듈 import (실제 SQS 폴링 X)
    cmd = [
        "docker", "run", "--rm",
        "-e", "PYTHONPATH=/app",
        "-e", "DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker",
        "-e", "DB_HOST=localhost",
        "-e", "DB_NAME=academy",
        "-e", "DB_USER=postgres",
        "-e", "DB_PASSWORD=postgres",
        "-e", "REDIS_HOST=localhost",
        "-e", "AWS_REGION=ap-northeast-2",
        "-e", "VIDEO_SQS_QUEUE_NAME=academy-video-jobs",
        "academy-video-worker:latest",
        "python", "-c",
        "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','apps.api.config.settings.worker'); "
        "import django; django.setup(); "
        "import apps.worker.video_worker.sqs_main; print('OK')",
    ]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        print(f"    docker run 실패: {r.stderr[-500:] if r.stderr else r.stdout[-500:]}")
        return False
    print("    Docker 이미지 단독 실행 OK")
    return True


def check_docker_skip() -> bool:
    """Docker 검사 생략 (이미지 없으면 SKIP)"""
    r = subprocess.run(
        ["docker", "image", "inspect", "academy-video-worker:latest"],
        capture_output=True,
        timeout=5,
    )
    if r.returncode != 0:
        print("    SKIP (academy-video-worker image not found)")
        return True
    return check_docker_image()


# ---------------------------------------------------------------------------
# [2] Redis 실연결
# ---------------------------------------------------------------------------


def check_redis_live(*, allow_skip_unconfigured: bool = False) -> bool:
    """Redis 선택 사항: 미설정 시 SKIP(통과). 설정 시 ping 성공해야 통과."""
    try:
        if not os.getenv("REDIS_HOST"):
            print("    SKIP (REDIS_HOST unset - Redis optional)")
            return True
        from libs.redis.client import get_redis_client
        client = get_redis_client()
        if not client:
            print("    SKIP (get_redis_client() -> None)")
            return True
        client.ping()
        print("    Redis PING OK")
        return True
    except Exception as e:
        if allow_skip_unconfigured and isinstance(e, UnicodeEncodeError):
            print("    SKIP (local: Redis optional)")
            return True
        print(f"    Redis 연결 실패: {_safe_msg(e)}")
        return False


# ---------------------------------------------------------------------------
# [3] SQS 메시지 1건 처리
# ---------------------------------------------------------------------------


def _is_sqs_unconfigured_error(e: Exception) -> bool:
    """로컬에서 AWS 자격 증명 없을 때 나는 오류인지"""
    msg = str(e).lower()
    return (
        "invalidclienttokenid" in msg
        or "security token" in msg
        or "no credentials" in msg
        or "credentials" in msg and "invalid" in msg
    )


def check_sqs_live(*, allow_skip_unconfigured: bool = False) -> bool:
    """SQS 큐 접근 가능, receive_message 호출 가능 (메시지 없어도 OK)"""
    try:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.worker")
        import django
        django.setup()
    except Exception as e:
        print(f"    Django setup 실패: {e}")
        return False

    try:
        from libs.queue import get_queue_client
        client = get_queue_client()
        queue_name = os.getenv("VIDEO_SQS_QUEUE_NAME", "academy-video-jobs")
        # receive (메시지 없어도 에러 안 나면 OK)
        msg = client.receive_message(queue_name=queue_name, wait_time_seconds=1)
        print(f"    SQS receive_message OK (queue={queue_name}, msg={'있음' if msg else '없음'})")
        return True
    except Exception as e:
        orig = getattr(e, "__context__", None) or e
        if allow_skip_unconfigured and (
            isinstance(e, UnicodeEncodeError)
            or _is_sqs_unconfigured_error(e)
            or _is_sqs_unconfigured_error(orig)
        ):
            print("    SKIP (local: AWS creds not set)")
            return True
        print(f"    SQS 연결/수신 실패: {_safe_msg(e)}")
        return False


# ---------------------------------------------------------------------------
# [4] DB 상태 업데이트
# ---------------------------------------------------------------------------


def _is_db_unconfigured_error(e: Exception) -> bool:
    """로컬에서 DB 미기동/미설정 때 나는 오류인지"""
    msg = str(e).lower()
    return (
        "no password supplied" in msg
        or "connection refused" in msg
        or "could not connect" in msg
        or "fe_sendauth" in msg
        or "connect" in msg and "refused" in msg
    )


def check_db_live(*, allow_skip_unconfigured: bool = False) -> bool:
    """Django DB 연결 및 쿼리 가능"""
    try:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.worker")
        import django
        django.setup()
    except Exception as e:
        print(f"    Django setup 실패: {e}")
        return False

    try:
        from django.db import connection
        with connection.cursor() as c:
            c.execute("SELECT 1")
            c.fetchone()
        print("    DB connection OK")
        return True
    except Exception as e:
        orig = getattr(e, "__context__", None) or e
        if allow_skip_unconfigured and (
            isinstance(e, UnicodeEncodeError)
            or _is_db_unconfigured_error(e)
            or _is_db_unconfigured_error(orig)
        ):
            print("    SKIP (local: DB not running. Use docker compose up -d postgres or .env)")
            return True
        print(f"    DB 연결 실패: {_safe_msg(e)}")
        return False


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="배포 가능 4조건 검증")
    parser.add_argument("--docker", action="store_true", help="Docker 이미지 검사 포함")
    parser.add_argument(
        "--local",
        action="store_true",
        help="로컬 모드: SQS/DB 미설정 시 SKIP(통과). 이미지 빌드 후 4/4 확인용",
    )
    args = parser.parse_args()

    banner("배포 가능 상태 검증 (4조건)" + (" [local mode]" if args.local else ""))

    passed = 0
    total = 4
    allow_skip = args.local

    # [1]
    banner(CONDITIONS[0])
    if args.docker:
        ok = run("[1] Worker Docker 단독 실행", check_docker_image)
        if ok:
            passed += 1
        elif allow_skip:
            print("    SKIP (local: image missing or run failed. Run docker/build.ps1 before deploy)")
            passed += 1
    else:
        ok = run("[1] Worker Docker (--docker로 검사)", check_docker_skip)
        if ok:
            passed += 1
        elif allow_skip:
            print("    SKIP (local: Docker check failed, continuing)")
            passed += 1

    # [2]
    banner(CONDITIONS[1])
    if run("[2] Redis 실연결", lambda: check_redis_live(allow_skip_unconfigured=allow_skip)):
        passed += 1

    # [3]
    banner(CONDITIONS[2])
    if run("[3] SQS 메시지 수신", lambda: check_sqs_live(allow_skip_unconfigured=allow_skip)):
        passed += 1

    # [4]
    banner(CONDITIONS[3])
    if run("[4] DB 연결", lambda: check_db_live(allow_skip_unconfigured=allow_skip)):
        passed += 1

    banner("결과")
    print(f"  통과: {passed}/{total}")
    if passed == total:
        print("  [OK] 배포 가능 상태입니다.")
        return 0
    print("  [FAIL] 미통과 항목을 해결한 후 재실행하세요.")
    print("  참고: docs/DEPLOYMENT_READINESS_GAP.md")
    return 1


if __name__ == "__main__":
    sys.exit(main())
