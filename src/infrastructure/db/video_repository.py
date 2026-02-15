"""
DEPRECATED: ORM 의존성 제거됨 (Gate 7).

Video 상태 업데이트는 academy adapters를 사용하세요:
  from academy.adapters.db.django.repositories_video import DjangoVideoRepository
"""
from __future__ import annotations

def __getattr__(name: str):
    raise ImportError(
        "src.infrastructure.db.video_repository is deprecated. "
        "Use academy.adapters.db.django.repositories_video.DjangoVideoRepository instead."
    )
