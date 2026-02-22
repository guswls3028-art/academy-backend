"""
delete_r2 Lambda: SQS academy-video-jobs 메시지 중 action=delete_r2 처리.
POST /api/v1/internal/video/delete-r2/ 호출.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

logger = logging.getLogger()
logger.setLevel(logging.INFO)

API_BASE = os.environ.get("VIDEO_DELETE_R2_API_URL", "").rstrip("/")
LAMBDA_INTERNAL_API_KEY = os.environ.get("LAMBDA_INTERNAL_API_KEY", "")


def lambda_handler(event, context):
    processed = 0
    failed = 0

    for record in event.get("Records", []):
        try:
            body = json.loads(record.get("body", "{}"))
            if body.get("action") != "delete_r2":
                continue

            video_id = body.get("video_id")
            file_key = body.get("file_key", "")
            hls_prefix = body.get("hls_prefix", "")

            if not API_BASE:
                logger.warning("VIDEO_DELETE_R2_API_URL not set; skipping")
                failed += 1
                continue

            url = f"{API_BASE}/api/v1/internal/video/delete-r2/"
            headers = {"Content-Type": "application/json"}
            if LAMBDA_INTERNAL_API_KEY:
                headers["X-Internal-Key"] = LAMBDA_INTERNAL_API_KEY

            data = json.dumps({
                "video_id": video_id,
                "file_key": file_key or "",
                "hls_prefix": hls_prefix or "",
            }).encode()
            req = urllib.request.Request(url, data=data, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                if 200 <= resp.status < 300:
                    processed += 1
                else:
                    failed += 1
        except Exception as e:
            logger.exception("delete_r2 failed: %s", e)
            failed += 1

    return {"processed": processed, "failed": failed}
