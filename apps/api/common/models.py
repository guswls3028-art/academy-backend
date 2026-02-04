# PATH: apps/api/common/models.py
from django.db import models


class TimestampModel(models.Model):
    """
    생성 / 수정 시간 자동 기록 추상 모델
    """
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BaseModel(TimestampModel):
    """
    모든 모델이 상속하는 공통 베이스 모델.

    - 공통 타임스탬프 포함
    - 추후 SoftDelete, Taggable 등의 기능 확장 가능
    """
    class Meta:
        abstract = True
