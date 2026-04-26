# Data migration (M-2): MatchupDocument를 InventoryFile에 연결.
# storage-as-canonical 마이그레이션의 데이터 단계.
# idempotent — 이미 연결된 row는 건너뜀. r2_key 중복 시 기존 row에 연결.
# 동일 로직: management/commands/backfill_matchup_inventory.py (수동 재실행/디버깅용).

from django.db import migrations


ROOT_FOLDER_NAME = "매치업-자동등록"


def backfill_inventory_files(apps, schema_editor):
    MatchupDocument = apps.get_model("matchup", "MatchupDocument")
    InventoryFolder = apps.get_model("inventory", "InventoryFolder")
    InventoryFile = apps.get_model("inventory", "InventoryFile")

    pending = MatchupDocument.objects.filter(inventory_file__isnull=True).select_related("tenant")
    if not pending.exists():
        return

    # tenant별 folder 캐시
    root_cache = {}
    ym_cache = {}

    for doc in pending:
        tenant = doc.tenant

        # root folder
        if tenant.id not in root_cache:
            root, _ = InventoryFolder.objects.get_or_create(
                tenant=tenant, scope="admin", student_ps="",
                parent=None, name=ROOT_FOLDER_NAME,
            )
            root_cache[tenant.id] = root
        root = root_cache[tenant.id]

        # year-month folder
        ym_key = doc.created_at.strftime("%Y-%m") if doc.created_at else "unknown"
        ym_cache_key = (tenant.id, ym_key)
        if ym_cache_key not in ym_cache:
            ym_folder, _ = InventoryFolder.objects.get_or_create(
                tenant=tenant, scope="admin", student_ps="",
                parent=root, name=ym_key,
            )
            ym_cache[ym_cache_key] = ym_folder
        ym_folder = ym_cache[ym_cache_key]

        # 동일 r2_key가 이미 InventoryFile에 있으면 그 row에 연결
        existing = InventoryFile.objects.filter(tenant=tenant, r2_key=doc.r2_key).first()
        if existing:
            doc.inventory_file_id = existing.id
            doc.save(update_fields=["inventory_file"])
            continue

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
        doc.save(update_fields=["inventory_file"])


def reverse_noop(apps, schema_editor):
    # 역방향: InventoryFile은 그대로 두고 link만 끊음 (r2 객체 보호).
    MatchupDocument = apps.get_model("matchup", "MatchupDocument")
    MatchupDocument.objects.filter(inventory_file__isnull=False).update(inventory_file=None)


class Migration(migrations.Migration):

    dependencies = [
        ("matchup", "0005_matchupdocument_inventory_file"),
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill_inventory_files, reverse_noop, elidable=True),
    ]
