from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings


@override_settings(
    CORS_ALLOWED_ORIGINS=[
        "https://hakwonplus.com",
        "https://godmin.kr",
        "https://godmin.kr",
        "",
    ],
    R2_ENDPOINT="https://r2.example.invalid",
    R2_ACCESS_KEY="test-access-key",
    R2_SECRET_KEY="test-secret-key",
    R2_REGION="auto",
    R2_VIDEO_BUCKET="academy-video",
)
@patch("apps.core.management.commands.setup_r2_cors.boto3.client")
def test_setup_r2_cors_uses_approved_api_origins(mock_boto_client):
    storage_client = mock_boto_client.return_value

    call_command("setup_r2_cors")

    storage_client.put_bucket_cors.assert_called_once_with(
        Bucket="academy-video",
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedOrigins": [
                        "https://hakwonplus.com",
                        "https://godmin.kr",
                    ],
                    "AllowedMethods": ["GET", "PUT", "POST", "HEAD", "DELETE"],
                    "AllowedHeaders": ["*"],
                    "ExposeHeaders": ["ETag", "Content-Length"],
                    "MaxAgeSeconds": 3600,
                }
            ]
        },
    )


@override_settings(
    CORS_ALLOWED_ORIGINS=[],
    R2_VIDEO_BUCKET="academy-video",
)
@patch("apps.core.management.commands.setup_r2_cors.boto3.client")
def test_setup_r2_cors_refuses_to_clear_all_origins(mock_boto_client):
    with pytest.raises(CommandError, match="CORS_ALLOWED_ORIGINS is empty"):
        call_command("setup_r2_cors")

    mock_boto_client.assert_not_called()
