import json

import boto3
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _configured_allowed_origins() -> list[str]:
    origins = list(
        dict.fromkeys(
            origin.strip()
            for origin in getattr(settings, "CORS_ALLOWED_ORIGINS", [])
            if isinstance(origin, str) and origin.strip()
        )
    )
    if not origins:
        raise CommandError(
            "CORS_ALLOWED_ORIGINS is empty; refusing to overwrite the R2 CORS policy."
        )
    return origins


class Command(BaseCommand):
    help = "R2 버킷 CORS 설정 (비디오 업로드용)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--bucket",
            type=str,
            help="버킷 이름 (기본값: R2_VIDEO_BUCKET)",
        )

    def handle(self, *args, **options):
        bucket_name = options.get("bucket") or getattr(
            settings, "R2_VIDEO_BUCKET", "academy-video"
        )
        allowed_origins = _configured_allowed_origins()

        s3 = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT,
            aws_access_key_id=settings.R2_ACCESS_KEY,
            aws_secret_access_key=settings.R2_SECRET_KEY,
            region_name=getattr(settings, "R2_REGION", "auto") or "auto",
        )

        # CORS 설정
        cors_config = {
            "CORSRules": [
                {
                    "AllowedOrigins": allowed_origins,
                    "AllowedMethods": ["GET", "PUT", "POST", "HEAD", "DELETE"],
                    "AllowedHeaders": ["*"],
                    "ExposeHeaders": ["ETag", "Content-Length"],
                    "MaxAgeSeconds": 3600,
                }
            ]
        }

        try:
            s3.put_bucket_cors(Bucket=bucket_name, CORSConfiguration=cors_config)
            self.stdout.write(
                self.style.SUCCESS(f"✅ R2 버킷 '{bucket_name}' CORS 설정 완료")
            )
            self.stdout.write(f"CORS 설정:\n{json.dumps(cors_config, indent=2)}")
        except Exception as e:
            error_msg = str(e)
            if "AccessDenied" in error_msg:
                self.stdout.write(
                    self.style.WARNING(
                        "⚠️ 권한이 없습니다. Cloudflare 대시보드에서 직접 설정해주세요:\n"
                        f"1. Cloudflare Dashboard → R2 → {bucket_name} 버킷 선택\n"
                        "2. Settings → CORS Policy\n"
                        "3. 다음 JSON 설정 추가:\n"
                        + json.dumps(cors_config, indent=2, ensure_ascii=False)
                    )
                )
            else:
                self.stdout.write(
                    self.style.ERROR(f"CORS 설정 실패: {error_msg}")
                )
            raise
