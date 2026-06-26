"""
R2 storage 버킷의 매치업 prefix에서 DB와 매칭되지 않는 orphan 파일 스캔.

설계 원칙 (학원장 데이터 손실 방지):
  - 본 cmd 는 DRY-RUN 전용. delete 코드 없음.
  - tenants/*/matchup/ prefix 만 대상. archive/admin/exams/clinic/students 제외.
  - R2 LastModified >= 7일 (168h) 인 파일만 orphan 후보 (race 회피).
  - DB 키 세트는 매치업 도메인 4 컬럼 + meta JSON 모두 집계 (누락 시 false orphan).

수집되는 DB 키:
  1. MatchupDocument.r2_key
  2. MatchupProblem.image_key + meta.public_cleanup.public_image_key
  3. ProblemSegmentationProposal.image_key
  4. MatchupHitReport.image_key (있다면)
  5. MatchupDocument.meta["page_image_keys"] (list)
  6. InventoryFile.r2_key (matchup prefix 가리킬 수 있음)

결과 리포트:
  - 총 매치업 prefix 객체 수 / 총 GB
  - DB-known 객체 / GB
  - orphan 후보 객체 / GB (>= min-age)
  - too_young (skipped)
  - 도메인별 sub-prefix 분포 (problems/pages/inventory/...)
  - orphan 후보 샘플 10건 (key, size, LastModified)

사용 예:
  python manage.py scan_orphan_matchup_storage              # dry-run report
  python manage.py scan_orphan_matchup_storage --min-age-hours 720  # 30일 이상만
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Scan orphan files under tenants/*/matchup/ in R2 storage bucket (dry-run only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-age-hours", type=int, default=168,
            help="Minimum R2 LastModified age in hours (default 168 = 7d).",
        )
        parser.add_argument(
            "--sample-size", type=int, default=20,
            help="Sample orphan keys to print (default 20).",
        )
        parser.add_argument(
            "--tenant", type=int, default=None,
            help="Scan only this tenant id. REQUIRED — set explicitly to avoid "
                 "accidental cross-tenant scan that could expose 학원장 cuts as orphan.",
        )
        parser.add_argument(
            "--allow-all-tenants", action="store_true",
            help="Bypass tenant requirement and scan ALL tenants (DANGER: 학원장 manual "
                 "cut keys in other tenants will appear in the report).",
        )

    def handle(self, *args, **opts):
        min_age = timedelta(hours=opts["min_age_hours"])
        sample_size = int(opts["sample_size"])
        only_tenant = opts["tenant"]
        allow_all = opts["allow_all_tenants"]

        if only_tenant is None and not allow_all:
            raise CommandError(
                "--tenant <id> required. Use --allow-all-tenants only if you "
                "intend to scan every tenant (dry-run still). 학원장 manual cut "
                "보호를 위해 명시적 tenant 지정 필수."
            )

        bucket = getattr(settings, "R2_STORAGE_BUCKET", None)
        if not bucket:
            raise CommandError("R2_STORAGE_BUCKET not configured")

        import boto3
        s3 = boto3.client(
            "s3",
            region_name="auto",
            endpoint_url=settings.R2_ENDPOINT,
            aws_access_key_id=settings.R2_ACCESS_KEY,
            aws_secret_access_key=settings.R2_SECRET_KEY,
        )

        db_keys = self._collect_db_keys(only_tenant=only_tenant)

        self.stdout.write(self.style.HTTP_INFO("=== DB known keys ==="))
        self.stdout.write(f"total db-known keys (matchup-related): {len(db_keys)}")
        manual_n = getattr(self, "_manual_protected_count", 0)
        self.stdout.write(self.style.SUCCESS(
            f"  ↳ 학원장 manual cut/paste protected: {manual_n} keys"
        ))

        now = timezone.now()
        total_objects = 0
        total_bytes = 0
        known_objects = 0
        known_bytes = 0
        too_young = 0
        orphan_keys: list[tuple[str, int, str]] = []  # (key, size, lastmodified_iso)
        sub_prefix_count: dict[str, int] = {}
        sub_prefix_bytes: dict[str, int] = {}

        # tenant 필터 적용
        if only_tenant is not None:
            prefixes = [f"tenants/{only_tenant}/matchup/"]
        else:
            prefixes = ["tenants/"]

        paginator = s3.get_paginator("list_objects_v2")
        for prefix in prefixes:
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents") or []:
                    k = obj.get("Key") or ""
                    if "/matchup/" not in k:
                        continue
                    sz = int(obj.get("Size") or 0)
                    lm = obj.get("LastModified")
                    total_objects += 1
                    total_bytes += sz

                    sub = self._sub_prefix(k)
                    sub_prefix_count[sub] = sub_prefix_count.get(sub, 0) + 1
                    sub_prefix_bytes[sub] = sub_prefix_bytes.get(sub, 0) + sz

                    if k in db_keys:
                        known_objects += 1
                        known_bytes += sz
                        continue

                    if lm is not None and (now - lm) < min_age:
                        too_young += 1
                        continue

                    orphan_keys.append((k, sz, lm.isoformat() if lm else ""))

        orphan_bytes = sum(s for _, s, _ in orphan_keys)

        def gb(b: int) -> str:
            return f"{b / 1024 / 1024 / 1024:.2f} GB"

        def mb(b: int) -> str:
            return f"{b / 1024 / 1024:.1f} MB"

        self.stdout.write(self.style.HTTP_INFO("=== SCAN RESULT ==="))
        self.stdout.write(f"bucket={bucket} min_age_hours={opts['min_age_hours']} tenant={only_tenant or 'ALL'}")
        self.stdout.write(f"total matchup objects: {total_objects} ({gb(total_bytes)})")
        self.stdout.write(f"db-known matched: {known_objects} ({gb(known_bytes)})")
        self.stdout.write(f"too young (skipped): {too_young}")
        self.stdout.write(self.style.WARNING(
            f"orphan candidates: {len(orphan_keys)} ({gb(orphan_bytes)})"
        ))

        self.stdout.write(self.style.HTTP_INFO("\n=== SUB-PREFIX DISTRIBUTION ==="))
        for sub in sorted(sub_prefix_count.keys()):
            self.stdout.write(
                f"  {sub}: {sub_prefix_count[sub]} objects ({mb(sub_prefix_bytes[sub])})"
            )

        if orphan_keys:
            orphan_keys.sort(key=lambda x: -x[1])  # size desc
            self.stdout.write(self.style.HTTP_INFO(f"\n=== TOP {sample_size} ORPHAN BY SIZE ==="))
            for k, sz, lm in orphan_keys[:sample_size]:
                self.stdout.write(f"  {mb(sz):>10}  {lm[:19]}  {k}")

        self.stdout.write(self.style.WARNING(
            "\nDRY-RUN only. Review sample + sub-prefix distribution before designing a delete pass."
        ))

    @staticmethod
    def _sub_prefix(key: str) -> str:
        # tenants/{tid}/matchup/{uuid}/{sub}/...
        parts = key.split("/")
        if len(parts) >= 5:
            return parts[4]  # problems / pages / inventory / etc.
        return "(root)"

    def _collect_db_keys(self, only_tenant: int | None) -> set[str]:
        """모든 매치업 관련 R2 키 수집. 학원장 manual cut(meta.manual=True) 키는
        명시적으로 protected set으로도 따로 카운트해서 report에 노출 → false orphan 0 보장.
        """
        from apps.domains.matchup.models import (
            MatchupDocument,
            MatchupProblem,
        )
        keys: set[str] = set()
        manual_keys: set[str] = set()  # 학원장 직접 cut/paste keys — 절대 orphan 아님

        doc_qs = MatchupDocument.objects.all()
        if only_tenant is not None:
            doc_qs = doc_qs.filter(tenant_id=only_tenant)

        # 1. MatchupDocument.r2_key + meta.page_image_keys
        for doc in doc_qs.only("r2_key", "meta"):
            if doc.r2_key:
                keys.add(doc.r2_key)
            page_keys = (doc.meta or {}).get("page_image_keys") or []
            if isinstance(page_keys, list):
                for k in page_keys:
                    if isinstance(k, str) and k:
                        keys.add(k)

        # 2. MatchupProblem.image_key + public cleanup image (+ manual=True 별도 set)
        prob_qs = MatchupProblem.objects.exclude(image_key="")
        if only_tenant is not None:
            prob_qs = prob_qs.filter(tenant_id=only_tenant)
        # 전체 keys + manual subset 동시 수집
        for image_key, meta in prob_qs.values_list("image_key", "meta"):
            if not image_key:
                continue
            keys.add(image_key)
            if isinstance(meta, dict):
                cleanup = meta.get("public_cleanup")
                if isinstance(cleanup, dict):
                    public_key = cleanup.get("public_image_key")
                    if isinstance(public_key, str) and public_key:
                        keys.add(public_key)
            if isinstance(meta, dict) and meta.get("manual"):
                manual_keys.add(image_key)
        self._manual_protected_count = len(manual_keys)
        self._manual_protected_keys = manual_keys

        # 3. ProblemSegmentationProposal.image_key (있으면)
        try:
            from apps.domains.matchup.models import ProblemSegmentationProposal
            prop_qs = ProblemSegmentationProposal.objects.exclude(image_key="")
            if only_tenant is not None:
                prop_qs = prop_qs.filter(tenant_id=only_tenant)
            keys.update(prop_qs.values_list("image_key", flat=True))
        except (ImportError, AttributeError):
            pass

        # 4. MatchupHitReport: 현재 모델에는 image_key 컬럼 없음 (PDF on-demand generate).
        #    R2 영구 저장 없음 → DB 키 세트 기여 0.

        # 5. InventoryFile.r2_key — matchup prefix 가리킬 수 있음.
        try:
            from apps.domains.inventory.models import InventoryFile
            inv_qs = InventoryFile.objects.exclude(r2_key="")
            if only_tenant is not None:
                inv_qs = inv_qs.filter(tenant_id=only_tenant)
            keys.update(inv_qs.values_list("r2_key", flat=True))
        except (ImportError, AttributeError):
            pass

        # blanks 제거
        return {k for k in keys if isinstance(k, str) and k}
