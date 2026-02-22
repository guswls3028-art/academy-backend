#!/usr/bin/env python3
"""
배포 전 워커 검사

- baseline: 금지 패턴 참조 여부
- (선택) Docker 기반 import 검증 (--docker)

사용법:
  python scripts/check_workers.py           # baseline만 (로컬)
  python scripts/check_workers.py --docker  # Docker 이미지로 전체 검증
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

# Video = AWS Batch 전용. 로컬 import는 batch_main만 검증 (EC2/SQS 워커 아님).
WORKERS = [
    ("video_worker", "apps.worker.video_worker.batch_main", "Video Worker (Batch)"),
    ("ai_worker_cpu", "apps.worker.ai_worker.sqs_main_cpu", "AI Worker CPU (SQS)"),
    ("messaging_worker", "apps.worker.messaging_worker.sqs_main", "Messaging Worker (SQS)"),
]


def run_forbidden_import_check() -> bool:
    print("\n[0] check_worker_forbidden_imports ...")
    r = subprocess.run(
        [sys.executable, "scripts/check_worker_forbidden_imports.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(r.stdout or r.stderr)
        return False
    print("  OK")
    return True


def run_baseline_check() -> bool:
    print("\n[1/2] check_no_celery (baseline) ...")
    r = subprocess.run(
        [sys.executable, "scripts/check_no_celery.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        return False
    print("  OK")
    return True


def run_import_tests_local() -> bool:
    """로컬 환경에서 워커 모듈 import 검증 (Django + 의존성 필요)"""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.worker")
    try:
        import django
        django.setup()
    except Exception as e:
        print(f"\n[2/2] Django setup 실패: {e}")
        print("  pip install -r requirements/api.txt 후 재시도")
        return False
    print("\n[2/2] Worker 모듈 import 검증 ...")
    all_ok = True
    for name, module, desc in WORKERS:
        try:
            mod = __import__(module, fromlist=["main"])
            if not hasattr(mod, "main"):
                print(f"  FAIL {desc}: main() 없음")
                all_ok = False
                continue
            print(f"  OK  {desc}")
        except Exception as e:
            print(f"  FAIL {desc}: {e}")
            all_ok = False
    return all_ok


def run_docker_import_tests() -> bool:
    """Docker 이미지로 워커 import 검증 (배포 환경과 동일)"""
    # academy-base 없으면 worker 이미지 빌드 불가
    r = subprocess.run(
        ["docker", "image", "inspect", "academy-base:latest"],
        cwd=ROOT,
        capture_output=True,
        timeout=5,
    )
    if r.returncode != 0:
        print("\n[2/2] academy-base 이미지 없음. .\\docker\\build.ps1 실행")
        return False

    print("\n[2/2] Docker 기반 Worker import 검증 ...")
    # Video = Batch 전용. batch_main import만 검증 (실제 실행은 AWS Batch).
    for image, module, desc in [
        ("academy-video-worker:latest", "apps.worker.video_worker.batch_main", "Video Worker (Batch)"),
        ("academy-ai-worker-cpu:latest", "apps.worker.ai_worker.sqs_main_cpu", "AI Worker CPU"),
        ("academy-messaging-worker:latest", "apps.worker.messaging_worker.sqs_main", "Messaging Worker"),
    ]:
        cmd = [
            "docker", "run", "--rm",
            "-w", "/app",
            "-e", "PYTHONPATH=/app",
            "-e", "DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker",
            "-e", "DB_HOST=postgres",
            "-e", "DB_NAME=academy",
            "-e", "DB_USER=postgres",
            "-e", "DB_PASSWORD=postgres",
            "-e", "API_BASE_URL=http://localhost",
            "-e", "INTERNAL_WORKER_TOKEN=check",
            "-e", "R2_ACCESS_KEY=dummy",
            "-e", "R2_SECRET_KEY=dummy",
            "-e", "R2_ENDPOINT=http://localhost",
            image,
            "python", "-c",
            f"import django; django.setup(); import {module} as m; assert hasattr(m, 'main'); print('OK')",
        ]
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            print(f"  OK  {desc}")
        else:
            print(f"  FAIL {desc}: {r.stderr[:200] if r.stderr else r.stdout[:200]}")
            return False
    print("  Video 로그: CloudWatch /aws/batch/academy-video-worker")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="배포 전 워커 검사")
    parser.add_argument("--docker", action="store_true", help="Docker 이미지로 import 검증 (이미지 빌드 필요)")
    args = parser.parse_args()

    print("=== Worker 검사 (배포 전) ===")
    if not run_forbidden_import_check():
        print("\n[종료] forbidden import 검사 실패")
        return 1
    if not run_baseline_check():
        print("\n[종료] baseline 검사 실패")
        return 1

    if args.docker:
        if not run_docker_import_tests():
            print("\n[종료] Docker import 검증 실패")
            return 1
    else:
        print("\n[로컬] import 검증 생략 (--docker 로 Docker 기반 검증)")
        if run_import_tests_local():
            print("  로컬 import OK")
        else:
            print("  로컬 import 실패 → pip install -r requirements/api.txt 또는 --docker 권장")

    print("\n[완료] baseline 검사 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
