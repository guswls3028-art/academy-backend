# PATH: apps/infrastructure/storage/r2_storage.py
# Django Storage 백엔드 — R2(S3 호환) 사용
# 프로필 사진 등 Django FileField/ImageField에서 사용

from django.core.files.storage import Storage
from django.core.files.base import File
from django.conf import settings
from django.utils.deconstruct import deconstructible
import boto3
from botocore.exceptions import ClientError
from urllib.parse import urljoin


@deconstructible
class R2Storage(Storage):
    """R2(S3 호환) Storage 백엔드 — Django FileField/ImageField용"""

    def __init__(self, bucket_name=None):
        self.bucket_name = bucket_name or getattr(settings, "R2_STORAGE_BUCKET", "academy-storage")
        self.endpoint_url = getattr(settings, "R2_ENDPOINT", None)
        self.access_key = getattr(settings, "R2_ACCESS_KEY", None)
        self.secret_key = getattr(settings, "R2_SECRET_KEY", None)

    def _get_s3_client(self):
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name="auto",
        )

    def _open(self, name, mode="rb"):
        """파일 읽기 (presigned URL 사용)"""
        # Django FileField는 일반적으로 읽기 모드로만 사용
        # 실제 파일 내용이 필요할 때는 presigned URL을 사용
        raise NotImplementedError("R2Storage는 직접 파일 읽기를 지원하지 않습니다. presigned URL을 사용하세요.")

    def _save(self, name, content):
        """파일 저장"""
        s3 = self._get_s3_client()
        content_type = getattr(content, "content_type", None) or "application/octet-stream"
        
        # 파일 포인터를 처음으로 되돌림
        if hasattr(content, "seek"):
            content.seek(0)
        
        try:
            s3.upload_fileobj(
                Fileobj=content,
                Bucket=self.bucket_name,
                Key=name,
                ExtraArgs={"ContentType": content_type},
            )
            return name
        except Exception as e:
            raise IOError(f"R2 업로드 실패: {e}")

    def delete(self, name):
        """파일 삭제"""
        s3 = self._get_s3_client()
        try:
            s3.delete_object(Bucket=self.bucket_name, Key=name)
        except ClientError:
            pass  # 파일이 없어도 에러 발생하지 않음

    def exists(self, name):
        """파일 존재 여부 확인"""
        s3 = self._get_s3_client()
        try:
            s3.head_object(Bucket=self.bucket_name, Key=name)
            return True
        except ClientError:
            return False

    def url(self, name):
        """파일 URL (presigned URL 생성)"""
        # Django의 기본 url() 메서드는 절대 URL을 반환해야 함
        # 하지만 R2는 presigned URL이 필요하므로, 여기서는 기본 경로만 반환
        # 실제 URL은 serializer에서 presigned URL을 생성하여 사용
        from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage
        try:
            return generate_presigned_get_url_storage(key=name, expires_in=3600)
        except Exception:
            # 실패 시 기본 경로 반환
            return f"/media/{name}"

    def size(self, name):
        """파일 크기"""
        s3 = self._get_s3_client()
        try:
            response = s3.head_object(Bucket=self.bucket_name, Key=name)
            return response.get("ContentLength", 0)
        except ClientError:
            return 0
