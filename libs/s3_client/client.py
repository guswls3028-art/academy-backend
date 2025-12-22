import os
from typing import Tuple

import boto3
from botocore.exceptions import ClientError


AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET_NAME", "")
AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL")

_s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    endpoint_url=AWS_S3_ENDPOINT_URL or None,
)


def head_object(key: str) -> Tuple[bool, int]:
    """
    S3 object 존재 여부 + size(bytes)
    """
    if not AWS_S3_BUCKET:
        return False, 0

    try:
        resp = _s3.head_object(
            Bucket=AWS_S3_BUCKET,
            Key=key,
        )
        return True, int(resp.get("ContentLength") or 0)

    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False, 0
        return False, 0
