# PATH: src/infrastructure/storage/__init__.py
# R2(S3 호환) 객체 스토리지 어댑터 — IObjectStorage 구현
# Excel 파싱 워커 등에서 사용

from src.infrastructure.storage.r2_adapter import R2ObjectStorageAdapter

__all__ = ["R2ObjectStorageAdapter"]
