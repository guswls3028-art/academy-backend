# BlockType tenant별 시드: NOTICE, QNA, ERRATA, HOMEWORK

from django.db import migrations


DEFAULTS = [
    ("NOTICE", "공지", 1),
    ("QNA", "질의응답", 2),
    ("ERRATA", "오탈자", 3),
    ("HOMEWORK", "숙제", 4),
]


def seed_block_types(apps, schema_editor):
    BlockType = apps.get_model("community", "BlockType")
    Tenant = apps.get_model("core", "Tenant")
    for tenant in Tenant.objects.all():
        for code, label, order in DEFAULTS:
            BlockType.objects.get_or_create(
                tenant_id=tenant.id,
                code=code,
                defaults={"label": label, "order": order},
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("community", "0002_backfill_scope_nodes"),
        ("core", "0014_alter_user_phone_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_block_types, noop),
    ]
