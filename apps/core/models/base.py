# PATH: apps/core/models/base.py
"""
공통 베이스 모델 (TimestampModel, BaseModel)

Worker/API 공유 — apps.api 의존 제거를 위해 core에 정의.
"""
from django.db import models


class TimestampModel(models.Model):
    """생성/수정 시간 자동 기록 추상 모델"""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BaseModel(TimestampModel):
    """공통 타임스탬프 포함 베이스 모델"""
    class Meta:
        abstract = True
