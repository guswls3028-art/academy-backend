# PATH: apps/core/management/commands/delete_r2_legacy.py
"""
R2 버킷에서 레가시 prefix(또는 전체) 객체 일괄 삭제.

사용:
  # 레가시 prefix만 삭제 (기본 prefix='legacy/')
  python manage.py delete_r2_legacy

  # 통으로 다 지우기 (버킷 전체) — 경로/키 설정 다시 할 때
  python manage.py delete_r2_legacy --wipe --confirm-wipe
  python manage.py delete_r2_legacy --wipe --confirm-wipe --bucket video   # video만 전체 삭제
  python manage.py delete_r2_legacy --wipe --dry-run   # 전체 삭제 대상만 확인

  # 삭제 대상만 확인 (삭제 안 함)
  python manage.py delete_r2_legacy --dry-run

  # 특정 prefix 지정
  python manage.py delete_r2_legacy --prefix "old/"
  python manage.py delete_r2_legacy --bucket video --prefix "legacy/"
"""
from __future__ import annotations

import logging
from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

# delete_objects 최대 1000개
DELETE_BATCH = 1000
LIST_PAGE = 1000


def get_s3_client():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT,
        aws_access_key_id=settings.R2_ACCESS_KEY,
        aws_secret_access_key=settings.R2_SECRET_KEY,
        region_name="auto",
    )


def get_bucket_name(bucket_key: str) -> str:
    if bucket_key == "ai":
        return getattr(settings, "R2_AI_BUCKET", "academy-ai")
    if bucket_key == "video":
        return getattr(settings, "R2_VIDEO_BUCKET", "academy-video")
    if bucket_key == "storage":
        return getattr(settings, "R2_STORAGE_BUCKET", "academy-storage")
    raise ValueError(f"Unknown bucket key: {bucket_key}")


def list_and_delete_prefix(s3, bucket: str, prefix: str, dry_run: bool, stdout):
    total_listed = 0
    total_deleted = 0
    continuation_token = None

    while True:
        list_kw = {
            "Bucket": bucket,
            "MaxKeys": LIST_PAGE,
        }
        if prefix:
            list_kw["Prefix"] = prefix
        if continuation_token:
            list_kw["ContinuationToken"] = continuation_token

        resp = s3.list_objects_v2(**list_kw)
        contents = resp.get("Contents") or []
        total_listed += len(contents)

        if not contents:
            if not resp.get("IsTruncated"):
                break
            continuation_token = resp.get("NextContinuationToken")
            continue

        keys_to_delete = [{"Key": obj["Key"]} for obj in contents]
        if dry_run:
            for k in keys_to_delete[:5]:
                stdout.write(f"  (dry-run) would delete: {k['Key']}")
            if len(keys_to_delete) > 5:
                stdout.write(f"  ... and {len(keys_to_delete) - 5} more in this batch")
            total_deleted += len(keys_to_delete)
        else:
            # 최대 1000개씩 삭제
            for i in range(0, len(keys_to_delete), DELETE_BATCH):
                batch = keys_to_delete[i : i + DELETE_BATCH]
                s3.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": batch, "Quiet": True},
                )
                total_deleted += len(batch)
                stdout.write(f"  Deleted batch of {len(batch)} objects")

        if not resp.get("IsTruncated"):
            break
        continuation_token = resp.get("NextContinuationToken")

    return total_listed, total_deleted


class Command(BaseCommand):
    help = "R2 버킷에서 레가시(또는 지정 prefix) 객체 일괄 삭제. --dry-run 으로 대상만 확인."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prefix",
            type=str,
            default="legacy/",
            help="삭제할 객체 prefix (기본: legacy/). 전체 버킷은 빈 문자열 ''.",
        )
        parser.add_argument(
            "--bucket",
            type=str,
            choices=["ai", "video", "storage", "all"],
            default="all",
            help="대상 버킷 (기본: all)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="삭제하지 않고 대상 키만 출력",
        )
        parser.add_argument(
            "--wipe",
            action="store_true",
            help="버킷 전체 삭제 (prefix 없음). 사용 시 --confirm-wipe 필요 (--dry-run 제외).",
        )
        parser.add_argument(
            "--confirm-wipe",
            action="store_true",
            help="전체 삭제(--wipe) 실행 확인. 없으면 전체 삭제 시 중단.",
        )

    def handle(self, *args, **options):
        prefix = options["prefix"]
        if options.get("wipe"):
            prefix = ""
        bucket_key = options["bucket"]
        dry_run = options["dry_run"]
        confirm_wipe = options.get("confirm_wipe", False)

        # 버킷 전체 삭제 시 확인 필수 (dry-run은 괜찮음)
        if prefix == "" and not dry_run and not confirm_wipe:
            self.stderr.write(
                self.style.ERROR(
                    "버킷 전체 삭제는 --confirm-wipe 를 함께 넣어야 합니다. "
                    "예: python manage.py delete_r2_legacy --wipe --confirm-wipe"
                )
            )
            return

        if not getattr(settings, "R2_ENDPOINT", None) or not getattr(settings, "R2_ACCESS_KEY", None):
            self.stderr.write(self.style.ERROR("R2_ENDPOINT / R2_ACCESS_KEY 가 설정되지 않았습니다."))
            return

        buckets = ["ai", "video", "storage"] if bucket_key == "all" else [bucket_key]
        s3 = get_s3_client()

        total_listed = 0
        total_deleted = 0

        for key in buckets:
            bucket = get_bucket_name(key)
            prefix_label = "(전체 버킷)" if prefix == "" else f"prefix={repr(prefix)}"
            self.stdout.write(f"Bucket: {bucket} (key={key}) {prefix_label} dry_run={dry_run}")
            try:
                listed, deleted = list_and_delete_prefix(s3, bucket, prefix, dry_run, self.stdout)
                total_listed += listed
                total_deleted += deleted
                self.stdout.write(self.style.SUCCESS(f"  Listed {listed}, deleted/would-delete {deleted}"))
            except Exception as e:
                try:
                    from botocore.exceptions import ClientError
                    if isinstance(e, ClientError) and (e.response.get("Error") or {}).get("Code") in ("AccessDenied", "403"):
                        self.stdout.write(self.style.WARNING(f"  Skip (Access Denied): {bucket} — 권한 없음, 다음 버킷 계속"))
                        continue
                except ImportError:
                    pass
                self.stderr.write(self.style.ERROR(f"  Error: {e}"))
                raise

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Total listed={total_listed}, deleted/would-delete={total_deleted}"
                + (" (dry-run, no changes)" if dry_run else "")
            )
        )
