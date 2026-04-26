# PATH: apps/domains/matchup/management/commands/backfill_matchup_inventory.py
"""
기존 MatchupDocument를 InventoryFile에 연결하는 backfill (M-2 단계).

설계: storage(InventoryFile)이 canonical, matchup이 그 위의 분석 레이어.
- 각 MatchupDocument에 대응하는 InventoryFile을 admin scope, /매치업-자동등록/{YYYY-MM}/ 폴더에 생성.
- r2_key는 동일하게 공유 (R2 객체 이동 없음 — 메타데이터만 등록).
- doc.inventory_file_id를 새 InventoryFile로 설정.
- 이미 r2_key가 InventoryFile에 있으면(중복) 그 row에 연결 (재실행 안전).

Usage:
  python manage.py backfill_matchup_inventory                  # 전체 backfill
  python manage.py backfill_matchup_inventory --tenant-id 1    # 특정 테넌트
  python manage.py backfill_matchup_inventory --dry-run        # 변경 없이 미리보기
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.domains.matchup.models import MatchupDocument
from apps.domains.inventory.models import InventoryFolder, InventoryFile


ROOT_FOLDER_NAME = "매치업-자동등록"


class Command(BaseCommand):
    help = "기존 MatchupDocument를 InventoryFile에 연결 (storage-as-canonical 마이그레이션)"

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        tenant_id = options["tenant_id"]
        dry_run = options["dry_run"]

        qs = MatchupDocument.objects.filter(inventory_file__isnull=True)
        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("backfill 대상 없음. 모든 MatchupDocument가 이미 inventory_file 연결됨."))
            return

        self.stdout.write(f"backfill 대상: {total}건 (dry-run={dry_run})")

        # tenant별 그룹핑
        per_tenant = defaultdict(list)
        for doc in qs.select_related("tenant"):
            per_tenant[doc.tenant_id].append(doc)

        created_files = 0
        linked_existing = 0
        errors = []

        for tid, docs in per_tenant.items():
            tenant = docs[0].tenant
            self.stdout.write(f"\n[Tenant {tid}] {len(docs)}건 처리")

            # 루트 폴더 확보
            root_folder = self._ensure_root_folder(tenant, dry_run)
            ym_cache: dict[str, InventoryFolder] = {}

            for doc in docs:
                ym_key = doc.created_at.strftime("%Y-%m") if doc.created_at else "unknown"
                ym_folder = ym_cache.get(ym_key)
                if ym_folder is None and root_folder is not None:
                    ym_folder = self._ensure_ym_folder(tenant, root_folder, ym_key, dry_run)
                    ym_cache[ym_key] = ym_folder

                try:
                    # 이미 동일 r2_key의 InventoryFile이 있는지 확인 (재실행 안전)
                    existing = InventoryFile.objects.filter(
                        tenant=tenant, r2_key=doc.r2_key,
                    ).first()
                    if existing:
                        if not dry_run:
                            doc.inventory_file_id = existing.id
                            doc.save(update_fields=["inventory_file", "updated_at"])
                        self.stdout.write(
                            f"  doc {doc.id} → 기존 InventoryFile {existing.id} 연결 (r2_key 중복)"
                        )
                        linked_existing += 1
                        continue

                    if dry_run:
                        self.stdout.write(
                            f"  [dry] doc {doc.id} ({doc.title[:30]}) → 신규 InventoryFile 생성 예정 in {ROOT_FOLDER_NAME}/{ym_key}/"
                        )
                        created_files += 1
                        continue

                    with transaction.atomic():
                        inv_file = InventoryFile.objects.create(
                            tenant=tenant,
                            scope="admin",
                            student_ps="",
                            folder=ym_folder,
                            display_name=doc.title or doc.original_name,
                            description="",
                            icon="file-text",
                            r2_key=doc.r2_key,
                            original_name=doc.original_name,
                            size_bytes=doc.size_bytes,
                            content_type=doc.content_type,
                        )
                        doc.inventory_file_id = inv_file.id
                        doc.save(update_fields=["inventory_file", "updated_at"])
                        created_files += 1
                        self.stdout.write(
                            f"  doc {doc.id} → InventoryFile {inv_file.id} 생성"
                        )
                except Exception as e:
                    errors.append((doc.id, str(e)))
                    self.stderr.write(f"  doc {doc.id} 실패: {e}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"완료: 신규 생성 {created_files}건 / 기존 연결 {linked_existing}건 / 오류 {len(errors)}건"
        ))
        if errors:
            self.stdout.write(self.style.ERROR("오류 목록:"))
            for doc_id, msg in errors[:20]:
                self.stdout.write(f"  doc {doc_id}: {msg}")

    def _ensure_root_folder(self, tenant, dry_run):
        existing = InventoryFolder.objects.filter(
            tenant=tenant, scope="admin", student_ps="",
            parent=None, name=ROOT_FOLDER_NAME,
        ).first()
        if existing:
            return existing
        if dry_run:
            self.stdout.write(f"  [dry] root folder '{ROOT_FOLDER_NAME}' 생성 예정")
            return None
        return InventoryFolder.objects.create(
            tenant=tenant, scope="admin", student_ps="",
            parent=None, name=ROOT_FOLDER_NAME,
        )

    def _ensure_ym_folder(self, tenant, root_folder, ym_key, dry_run):
        existing = InventoryFolder.objects.filter(
            tenant=tenant, scope="admin", student_ps="",
            parent=root_folder, name=ym_key,
        ).first()
        if existing:
            return existing
        if dry_run:
            self.stdout.write(f"  [dry] folder '{ROOT_FOLDER_NAME}/{ym_key}' 생성 예정")
            return None
        return InventoryFolder.objects.create(
            tenant=tenant, scope="admin", student_ps="",
            parent=root_folder, name=ym_key,
        )
