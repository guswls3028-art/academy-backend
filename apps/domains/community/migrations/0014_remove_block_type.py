# Generated for BlockType deprecation
"""BlockType FK + 모델 완전 제거. 0013에서 post_type backfill 완료된 상태 가정.

작업:
1. PostEntity.block_type FK 제거
2. PostTemplate.block_type FK 제거
3. BlockType 모델 자체 제거
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("community", "0013_backfill_post_type_from_block_type"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="postentity",
            name="block_type",
        ),
        migrations.RemoveField(
            model_name="posttemplate",
            name="block_type",
        ),
        migrations.DeleteModel(
            name="BlockType",
        ),
    ]
