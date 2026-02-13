# PATH: apps/api/common/models.py
# Re-export from core for backward compatibility (API 패키지 내 기존 import 유지)
from apps.core.models.base import BaseModel, TimestampModel

__all__ = ["TimestampModel", "BaseModel"]
