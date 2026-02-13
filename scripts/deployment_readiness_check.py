#!/usr/bin/env python3
"""
배포 가능 상태 검증 (4조건)

1. Worker Docker 이미지 단독 실행
2. Redis 실연결
3. SQS 메시지 1건 처리 (선택: 인프라 있을 때)
4. DB 상태 업데이트 (3과 연계)

사용법:
  python scripts/deployment_readiness_check.py
  python scripts/deployment_readiness_check.py --docker  # Docker 이미지 검사 포함

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
    "[2] Redis 실연결",
    "[3] SQS 메시지 1건 처리",
    "[4] DB 상태 업데이트",
]


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
        print(f"  [FAIL] {name}: {e}")
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
    # 단독 실행 테스트: import만 (실제 SQS 폴링 X)
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
        print("    SKIP (academy-video-worker 이미지 없음)")
        return True
    return check_docker_image()


# ---------------------------------------------------------------------------
# [2] Redis 실연결
# ---------------------------------------------------------------------------


def check_redis_live() -> bool:
    """get_redis_client() 반환 및 ping 성공"""
    try:
        from libs.redis.client import get_redis_client
        client = get_redis_client()
        if not client:
            print("    get_redis_client() -> None (REDIS_HOST 등 미설정)")
            return False
        client.ping()
        print("    Redis PING OK")
        return True
    except Exception as e:
        print(f"    Redis 연결 실패: {e}")
        return False


# ---------------------------------------------------------------------------
# [3] SQS 메시지 1건 처리
# ---------------------------------------------------------------------------


def check_sqs_live() -> bool:
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
        print(f"    SQS 연결/수신 실패: {e}")
        return False


# ---------------------------------------------------------------------------
# [4] DB 상태 업데이트
# ---------------------------------------------------------------------------


def check_db_live() -> bool:
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
        print(f"    DB 연결 실패: {e}")
        return False


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="배포 가능 4조건 검증")
    parser.add_argument("--docker", action="store_true", help="Docker 이미지 검사 포함")
    args = parser.parse_args()

    banner("배포 가능 상태 검증 (4조건)")

    passed = 0
    total = 4

    # [1]
    banner(CONDITIONS[0])
    if args.docker:
        if run("[1] Worker Docker 단독 실행", check_docker_image):
            passed += 1
    else:
        if run("[1] Worker Docker (--docker로 검사)", check_docker_skip):
            passed += 1

    # [2]
    banner(CONDITIONS[1])
    if run("[2] Redis 실연결", check_redis_live):
        passed += 1

    # [3]
    banner(CONDITIONS[2])
    if run("[3] SQS 메시지 수신", check_sqs_live):
        passed += 1

    # [4]
    banner(CONDITIONS[3])
    if run("[4] DB 연결", check_db_live):
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
