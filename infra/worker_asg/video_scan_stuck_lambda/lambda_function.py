"""
scan_stuck_video_jobs Lambda.

- EventBridge rate(2 minutes)로 호출.
- Worker ASG lifecycle과 독립.
- POST /api/v1/internal/video/scan-stuck/ 호출.
"""
from __future__ import annotations

import json
import os
import logging
import urllib.request
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

API_BASE = os.environ.get("VIDEO_BACKLOG_API_URL", "").rstrip("/")
LAMBDA_INTERNAL_API_KEY = os.environ.get("LAMBDA_INTERNAL_API_KEY", "")


def lambda_handler(event: dict, context: Any) -> dict:
    if not API_BASE:
        logger.warning("VIDEO_BACKLOG_API_URL not set; scan-stuck skipped")
        return {"recovered": 0, "dead": 0, "skipped": True}

    url = f"{API_BASE}/api/v1/internal/video/scan-stuck/"
    headers = {"Content-Type": "application/json"}
    if LAMBDA_INTERNAL_API_KEY:
        headers["X-Internal-Key"] = LAMBDA_INTERNAL_API_KEY

    try:
        body = json.dumps({"threshold": 3}).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            recovered = data.get("recovered", 0)
            dead = data.get("dead", 0)
            logger.info("scan-stuck done | recovered=%d dead=%d", recovered, dead)
            return {"recovered": recovered, "dead": dead}
    except Exception as e:
        logger.warning("scan-stuck API failed: %s", e)
        return {"recovered": 0, "dead": 0, "error": str(e)}
