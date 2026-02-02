# PATH: apps/support/video/constants.py

from __future__ import annotations


class VideoStatus:
    """
    ⚠️ SSOT: models.Video.Status 와 반드시 동일해야 한다.

    - worker/API/services에서 공통으로 쓰는 "외부 상수" 역할
    - 실제 DB 상태 변화는 항상 Video.Status를 기준으로 한다.
    """

    PENDING = "PENDING"
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"

    CHOICES = (
        (PENDING, "Pending"),
        (UPLOADED, "Uploaded"),
        (PROCESSING, "Processing"),
        (READY, "Ready"),
        (FAILED, "Failed"),
    )
