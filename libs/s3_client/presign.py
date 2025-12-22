import os
import boto3
from botocore.client import Config


AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET_NAME", "")
AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL")

AWS_PRESIGN_UPLOAD_EXPIRES = int(os.getenv("AWS_PRESIGN_UPLOAD_EXPIRES", "900"))
AWS_PRESIGN_STREAM_EXPIRES = int(os.getenv("AWS_PRESIGN_STREAM_EXPIRES", "3600"))

_s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    endpoint_url=AWS_S3_ENDPOINT_URL or None,
    config=Config(signature_version="s3v4"),
)


def create_presigned_put_url(
    key: str,
    content_type: str = "application/octet-stream",
    expires_in: int = AWS_PRESIGN_UPLOAD_EXPIRES,
) -> str:
    if not AWS_S3_BUCKET:
        raise RuntimeError("AWS_S3_BUCKET_NAME is not set")

    return _s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": AWS_S3_BUCKET,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=expires_in,
    )


def create_presigned_get_url(
    key: str,
    expires_in: int = AWS_PRESIGN_STREAM_EXPIRES,
) -> str:
    if not AWS_S3_BUCKET:
        raise RuntimeError("AWS_S3_BUCKET_NAME is not set")

    return _s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": AWS_S3_BUCKET,
            "Key": key,
        },
        ExpiresIn=expires_in,
    )
