# Generated for BlockType deprecation
"""post_type SSOT 이행 — 누락된 PostEntity.post_type을 block_type.code 기반으로 채움.

Idempotent: post_type이 이미 채워진 row는 skip.
모든 row의 post_type이 채워져야 다음 마이그레이션(0014)에서 block_type FK 제거 가능.
"""
from django.db import migrations


VALID_POST_TYPES = {"notice", "board", "materials", "qna", "counsel"}


def backfill_post_type(apps, schema_editor):
    PostEntity = apps.get_model("community", "PostEntity")

    # post_type 비어있거나 valid choices 외 값을 가진 row를 block_type.code 기반으로 채움
    qs = PostEntity.objects.filter(block_type__isnull=False).exclude(post_type__in=VALID_POST_TYPES)
    updated = 0
    for post in qs.select_related("block_type"):
        code = (getattr(post.block_type, "code", "") or "").strip().lower()
        new_type = code if code in VALID_POST_TYPES else "board"
        post.post_type = new_type
        post.save(update_fields=["post_type"])
        updated += 1
    print(f"[migration 0013] backfilled post_type for {updated} rows")

    # 그래도 비어있는 row는 default "board"
    fallback = PostEntity.objects.exclude(post_type__in=VALID_POST_TYPES).update(post_type="board")
    if fallback:
        print(f"[migration 0013] {fallback} rows fallback to 'board'")


def noop_reverse(apps, schema_editor):
    """역방향 — post_type 그대로 둠 (BlockType FK는 이미 제거됐을 수 있음)."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("community", "0012_postentity_meta"),
    ]

    operations = [
        migrations.RunPython(backfill_post_type, noop_reverse),
    ]
