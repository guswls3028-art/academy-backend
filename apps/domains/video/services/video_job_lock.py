"""
Compatibility facade for video job locking.

The DynamoDB implementation lives in academy.adapters.cache.dynamodb_video_job_lock.
"""

from __future__ import annotations

from academy.adapters.cache.dynamodb_video_job_lock import acquire, extend, release

__all__ = ["acquire", "extend", "release"]
