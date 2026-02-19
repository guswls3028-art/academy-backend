# PATH: apps/core/management/commands/setup_r2_cors.py
# R2 버킷 CORS 설정 자동화

from django.core.management.base import BaseCommand
from django.conf import settings
import boto3
import json


class Command(BaseCommand):
    help = "R2 버킷 CORS 설정 (비디오 업로드용)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--bucket",
            type=str,
            help="버킷 이름 (기본값: R2_VIDEO_BUCKET)",
        )

    def handle(self, *args, **options):
        bucket_name = options.get("bucket") or getattr(settings, "R2_VIDEO_BUCKET", "academy-video")
        
        s3 = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT,
            aws_access_key_id=settings.R2_ACCESS_KEY,
            aws_secret_access_key=settings.R2_SECRET_KEY,
            region_name="auto",
        )

        # CORS 설정
        cors_config = {
            "CORSRules": [
                {
                    "AllowedOrigins": [
                        "https://tchul.com",
                        "https://www.tchul.com",
                        "https://hakwonplus.com",
                        "https://www.hakwonplus.com",
                        "https://limglish.kr",
                        "https://www.limglish.kr",
                        "https://ymath.co.kr",
                        "https://www.ymath.co.kr",
                        "https://academy-frontend.pages.dev",
                        "http://localhost:5173",
                        "http://localhost:5174",
                    ],
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
                        "1. Cloudflare Dashboard → R2 → academy-video 버킷 선택\n"
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
