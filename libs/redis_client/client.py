# PATH: libs/redis_client/client.py

import redis
from django.conf import settings

REDIS_URL = getattr(settings, "REDIS_URL", None)

# ✅ Redis 미설정 환경(local/dev)에서는 클라이언트 생성 ❌
if not REDIS_URL:
    redis_client = None
else:
    redis_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=False,  # 🔥 네 말대로 정석 유지
    )
