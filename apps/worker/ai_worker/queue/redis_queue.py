import redis
import json
import time
import logging

logger = logging.getLogger(__name__)

class RedisJobQueue:
    def __init__(self, redis_url: str):
        self.r = redis.from_url(redis_url, decode_responses=True)
        self.jobs = "ai:jobs"
        self.processing = "ai:processing"
        self.dead = "ai:jobs:dead"

    def claim(self, timeout=5):
        return self.r.brpoplpush(self.jobs, self.processing, timeout)

    def ack(self, raw):
        self.r.lrem(self.processing, 1, raw)

    def retry_or_dead(self, job_dict, raw, reason):
        attempt = int(job_dict.get("attempt", 0)) + 1
        max_attempts = int(job_dict.get("max_attempts", 5))

        self.ack(raw)

        if attempt >= max_attempts:
            job_dict["error"] = reason
            self.r.lpush(self.dead, json.dumps(job_dict))
            return

        job_dict["attempt"] = attempt
        self.r.lpush(self.jobs, json.dumps(job_dict))
