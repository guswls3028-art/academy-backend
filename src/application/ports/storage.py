# PATH: src/application/ports/storage.py
# 객체 스토리지 포트 — R2/S3 Get·Delete (버킷명은 호출 시점에 주입)

from __future__ import annotations

from abc import ABC, abstractmethod
class IObjectStorage(ABC):
    """객체 스토리지 읽기/삭제 (버킷명 하드코딩 없음, 호출 시 전달)"""

    @abstractmethod
    def get_object(self, bucket: str, key: str) -> bytes:
        """버킷에서 객체 내용을 바이트로 반환."""
        ...

    @abstractmethod
    def download_to_path(self, bucket: str, key: str, local_path: str) -> None:
        """버킷에서 객체를 로컬 경로로 다운로드."""
        ...

    @abstractmethod
    def delete_object(self, bucket: str, key: str) -> None:
        """버킷에서 객체 삭제."""
        ...
