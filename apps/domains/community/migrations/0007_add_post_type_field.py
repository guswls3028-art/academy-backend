"""
Add post_type CharField to PostEntity, populate from block_type.code, make block_type nullable.
"""
from django.db import migrations, models
import django.db.models.deletion


def populate_post_type_from_block_type(apps, schema_editor):
    """For each PostEntity, set post_type from its block_type.code."""
    PostEntity = apps.get_model("community", "PostEntity")
    BlockType = apps.get_model("community", "BlockType")

    KNOWN_CODES = {"notice", "board", "materials", "qna", "counsel"}

    # Build a lookup: block_type_id -> code
    bt_map = {}
    for bt in BlockType.objects.all():
        code = (bt.code or "").strip().lower()
        bt_map[bt.id] = code if code in KNOWN_CODES else "board"

    # Batch update
    posts = PostEntity.objects.select_related().all()
    to_update = []
    for post in posts:
        mapped = bt_map.get(post.block_type_id, "board")
        post.post_type = mapped
        to_update.append(post)

    if to_update:
        PostEntity.objects.bulk_update(to_update, ["post_type"], batch_size=500)


def reverse_noop(apps, schema_editor):
    """Reverse: no-op (post_type will be dropped by reverse migration)."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("community", "0006_add_author_display_name"),
    ]

    operations = [
        # 1. Add post_type field with default "board"
        migrations.AddField(
            model_name="postentity",
            name="post_type",
            field=models.CharField(
                choices=[
                    ("notice", "공지사항"),
                    ("board", "게시판"),
                    ("materials", "자료실"),
                    ("qna", "QnA"),
                    ("counsel", "상담 신청"),
                ],
                default="board",
                db_index=True,
                help_text="게시글 유형 (notice, board, materials, qna, counsel)",
                max_length=20,
            ),
        ),
        # 2. Data migration: populate post_type from block_type.code
        migrations.RunPython(
            populate_post_type_from_block_type,
            reverse_code=reverse_noop,
        ),
        # 3. Make block_type nullable (SET_NULL instead of PROTECT)
        migrations.AlterField(
            model_name="postentity",
            name="block_type",
            field=models.ForeignKey(
                blank=True,
                help_text="레거시 블록 타입 FK (post_type으로 대체됨)",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="posts",
                to="community.blocktype",
            ),
        ),
        # 4. Update index: replace (tenant, block_type) with (tenant, post_type)
        migrations.RemoveIndex(
            model_name="postentity",
            name="community_p_tenant__0d6a8b_idx",
        ),
        migrations.AddIndex(
            model_name="postentity",
            index=models.Index(
                fields=["tenant", "post_type"],
                name="community_p_tenant__pt_idx",
            ),
        ),
    ]
