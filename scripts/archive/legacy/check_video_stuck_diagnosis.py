#!/usr/bin/env python3
"""
워커 3대 이상 떠있는데 일부 영상(111, 222 등)만 처리되고 나머지는 대기하는 경우 진단.

원인 후보:
1. Redis idempotency 락(job:encode:{video_id}:lock) 잔류 — 이전 워커가 크래시 후 미해제 (TTL 4h)
2. DB status != UPLOADED — mark_processing 실패 (이미 PROCESSING/READY/FAILED)
3. SQS 메시지 미전달 — enqueue 시점 문제 또는 큐 이슈

사용법:
  python scripts/check_video_stuck_diagnosis.py 111 222 333

  [Docker]
  docker exec -it academy-api python scripts/check_video_stuck_diagnosis.py 111 222 333
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.base")


def main() -> int:
    video_ids = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else []
    if not video_ids:
        print("Usage: python scripts/check_video_stuck_diagnosis.py <video_id> [video_id ...]")
        print("  e.g. python scripts/check_video_stuck_diagnosis.py 111 222 333")
        return 1

    import django
    django.setup()

    print("=" * 70)
    print("Video Stuck Diagnosis — 워커는 3대인데 일부만 처리될 때")
    print("=" * 70)

    # 1. DB status
    from academy.adapters.db.django.repositories_video import get_video_status
    print("\n[1] DB status")
    for vid in video_ids:
        status = get_video_status(vid)
        status_str = status or "(not found)"
        print(f"  video_id={vid} status={status_str}")

    # 2. Redis lock (job:encode:{video_id}:lock)
    print("\n[2] Redis idempotency lock (job:encode:{id}:lock)")
    try:
        from libs.redis.client import get_redis_client
        client = get_redis_client()
        if not client:
            print("  Redis not available (REDIS_HOST 미설정 또는 연결 실패)")
        else:
            for vid in video_ids:
                key = f"job:encode:{vid}:lock"
                ttl = client.ttl(key)
                if ttl == -2:
                    print(f"  video_id={vid} key={key} → 없음 (락 미보유)")
                else:
                    print(f"  video_id={vid} key={key} → TTL={ttl}s (락 잔류! 이전 워커 미해제 추정)")
    except Exception as e:
        print(f"  Redis 조회 실패: {e}")

    # 3. 해석 및 대응
    print("\n[3] 해석 및 대응")
    print("""
  - status=UPLOADED + Redis 락 없음
    → SQS 메시지가 안 들어갔거나, 이미 처리 후 skip 된 뒤 삭제됨.
    → API 재시도(POST /media/videos/{id}/retry/)로 다시 enqueue.

  - status=UPLOADED + Redis 락 있음 (TTL > 0)
    → 이전 워커가 크래시/킬 후 lock 미해제. TTL(기본 4h) 만료까지 대기하거나
    → Redis에서 해당 key 직접 삭제 후 retry:
       redis-cli DEL job:encode:111:lock job:encode:222:lock

  - status=PROCESSING
    → 워커가 mark_processing까지 했으나 인코딩 완료 전에 종료됨.
    → retry API 호출로 UPLOADED로 되돌리고 다시 enqueue.

  - status=READY
    → 이미 완료됨. 큐에 중복 메시지가 있었으면 delete되고 skip됨.

  - status=FAILED
    → 인코딩 실패. retry로 재시도 가능.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
