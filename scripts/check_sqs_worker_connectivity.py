#!/usr/bin/env python3
"""
워커 작업(영상 인코딩, 엑셀 수강등록) 실패 시 원인 규명용 진단 스크립트.

API 서버와 동일한 환경변수로 실행하여, SQS 큐 접근 가능 여부를 확인합니다.
- 영상: academy-video-jobs
- 엑셀(수강등록): academy-ai-jobs-basic

사용법:

  [Linux/EC2] API와 동일한 환경에서:
    cd /home/ec2-user/academy
    export DJANGO_SETTINGS_MODULE=apps.api.config.settings.base
    python3 scripts/check_sqs_worker_connectivity.py

  [Linux/EC2] API가 Docker로 실행 중일 때 (권장 — API와 동일 env):
    docker exec -it academy-api python scripts/check_sqs_worker_connectivity.py

  [Windows]
    cd C:\academy
    set DJANGO_SETTINGS_MODULE=apps.api.config.settings.base
    python scripts/check_sqs_worker_connectivity.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# API와 동일한 설정 사용 (base 또는 prod)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.base")


def _safe_msg(e: Exception) -> str:
    try:
        return str(e).encode("ascii", errors="replace").decode("ascii")
    except Exception:
        return repr(e)[:300]


def main() -> int:
    print("=" * 60)
    print("SQS 워커 연결 진단 (영상 / 엑셀 수강등록)")
    print("=" * 60)

    # 환경 변수 확인 (추론용)
    aws_region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    aws_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
    print(f"\n[환경] AWS_REGION={aws_region or '(미설정)'}")
    print(f"       AWS_ACCESS_KEY_ID={'설정됨' if aws_key else '(미설정)'}")
    print(f"       AWS_SECRET_ACCESS_KEY={'설정됨' if aws_secret else '(미설정)'}")
    if not aws_region and not aws_key:
        print("\n  → AWS 자격 증명이 없으면 SQS 접근이 불가합니다. API/워커 서버에")
        print("    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION(또는 AWS_DEFAULT_REGION) 설정 필요.")

    try:
        import django
        django.setup()
    except Exception as e:
        print(f"\n[오류] Django 설정 실패: {_safe_msg(e)}")
        return 1

    from libs.queue import get_queue_client, QueueUnavailableError

    client = get_queue_client()
    all_ok = True

    # 1) Video 큐
    video_queue = os.getenv("VIDEO_SQS_QUEUE_NAME", "academy-video-jobs")
    print(f"\n[1] Video 큐: {video_queue}")
    try:
        url = client._get_queue_url(video_queue)
        print(f"    get_queue_url: OK ({url[:60]}...)")
        # send_message는 실제 메시지가 쌓이므로 생략. get_queue_url 성공이면 권한은 있는 것.
        recv = client.receive_message(queue_name=video_queue, wait_time_seconds=1)
        print(f"    receive_message: OK (메시지 {'있음' if recv else '없음'})")
    except QueueUnavailableError as e:
        print(f"    FAIL: 큐 접근 불가 (자격 증명/권한 문제)")
        print(f"    상세: {_safe_msg(e)}")
        all_ok = False
    except Exception as e:
        err = _safe_msg(e)
        if "QueueDoesNotExist" in err or "NonExistentQueue" in err or "404" in err or "queue does not exist" in err.lower():
            print(f"    FAIL: 큐가 존재하지 않습니다. 리전(ap-northeast-2)에서 아래 스크립트로 생성 후 재시도:")
            print(f"      python scripts/create_sqs_resources.py")
        elif "AccessDenied" in err or "Access Denied" in err or "InvalidClientTokenId" in err or "SignatureDoesNotMatch" in err:
            print(f"    FAIL: AWS 자격 증명 오류 또는 SQS 권한 없음.")
            print(f"    상세: {err[:200]}")
        else:
            print(f"    FAIL: {err[:250]}")
        all_ok = False

    # 2) AI Basic 큐 (엑셀 수강등록)
    ai_basic_queue = os.getenv("AI_SQS_QUEUE_NAME_BASIC", "academy-ai-jobs-basic")
    print(f"\n[2] AI(Basic) 큐 (엑셀 수강등록): {ai_basic_queue}")
    try:
        url = client._get_queue_url(ai_basic_queue)
        print(f"    get_queue_url: OK ({url[:60]}...)")
        recv = client.receive_message(queue_name=ai_basic_queue, wait_time_seconds=1)
        print(f"    receive_message: OK (메시지 {'있음' if recv else '없음'})")
    except QueueUnavailableError as e:
        print(f"    FAIL: 큐 접근 불가 (자격 증명/권한 문제)")
        print(f"    상세: {_safe_msg(e)}")
        all_ok = False
    except Exception as e:
        err = _safe_msg(e)
        if "QueueDoesNotExist" in err or "NonExistentQueue" in err or "404" in err or "queue does not exist" in err.lower():
            print(f"    FAIL: 큐가 존재하지 않습니다. 리전(ap-northeast-2)에서 아래 스크립트로 생성 후 재시도:")
            print(f"      python scripts/create_ai_sqs_resources.py")
        elif "AccessDenied" in err or "Access Denied" in err or "InvalidClientTokenId" in err or "SignatureDoesNotMatch" in err:
            print(f"    FAIL: AWS 자격 증명 오류 또는 SQS 권한 없음.")
            print(f"    상세: {err[:200]}")
        else:
            print(f"    FAIL: {err[:250]}")
        all_ok = False

    print("\n" + "=" * 60)
    if all_ok:
        print("결과: SQS 연결 정상. 영상/엑셀 작업이 안 되면 다음을 확인하세요.")
        print("  - Video Worker 실행 여부: docker ps | findstr video-worker")
        print("  - AI Worker 실행 여부: docker ps | findstr ai-worker")
        print("  - API 로그: 영상 업로드 완료 시 'Video job enqueued' 또는 503/에러 메시지")
        return 0
    print("결과: SQS 연결 실패. 위 FAIL 원인을 해결한 뒤 API/워커를 재기동하세요.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
