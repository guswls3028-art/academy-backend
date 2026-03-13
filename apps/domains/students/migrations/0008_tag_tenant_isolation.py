"""
Tag 테넌트 격리: tenant FK 추가 + per-tenant unique constraint.

기존 Tag 레코드는 StudentTag → Student → tenant 관계를 통해 올바른 tenant로 배정.
어떤 Student 에도 연결되지 않은 고아 Tag 는 삭제.
"""
import django.db.models.deletion
from django.db import migrations, models


def backfill_tag_tenant(apps, schema_editor):
    """Assign each Tag to the tenant of a Student that uses it."""
    Tag = apps.get_model("students", "Tag")
    StudentTag = apps.get_model("students", "StudentTag")

    for tag in Tag.objects.filter(tenant__isnull=True):
        st = (
            StudentTag.objects
            .filter(tag=tag)
            .select_related("student")
            .first()
        )
        if st and st.student and st.student.tenant_id:
            tag.tenant_id = st.student.tenant_id
            tag.save(update_fields=["tenant_id"])
        else:
            # Orphan tag — no student uses it
            tag.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_tenant_og_fields"),
        ("students", "0007_hash_initial_password"),
    ]

    operations = [
        # 1. Add nullable tenant FK
        migrations.AddField(
            model_name="tag",
            name="tenant",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tags",
                to="core.tenant",
            ),
        ),
        # 2. Backfill tenant from StudentTag → Student → tenant
        migrations.RunPython(backfill_tag_tenant, migrations.RunPython.noop),
        # 3. Make tenant non-nullable
        migrations.AlterField(
            model_name="tag",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tags",
                to="core.tenant",
            ),
        ),
        # 4. Remove old global unique constraint
        migrations.RemoveConstraint(
            model_name="tag",
            name="uniq_tag_name",
        ),
        # 5. Add per-tenant unique constraint
        migrations.AddConstraint(
            model_name="tag",
            constraint=models.UniqueConstraint(
                fields=["tenant", "name"],
                name="uniq_tag_name_per_tenant",
            ),
        ),
    ]
