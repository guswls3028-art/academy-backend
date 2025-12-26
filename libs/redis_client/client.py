# libs/redis_client/client.py

import os
import redis
from django.conf import settings

redis_client = redis.Redis.from_url(
    settings.REDIS_URL,
    decode_responses=False,  # 🔥 이게 정석
)
