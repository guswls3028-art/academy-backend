"""
Backfill remaining null-tenant Exams to tenant 1, then enforce NOT NULL.

Background:
- 0008: Added nullable tenant FK
- 0009: Backfilled from session→lecture→tenant chain
- Still 43 exams with null tenant (orphan templates + early dev regulars without sessions)
- All are Tenant 1 (dev/test) era data — safe to assign to tenant 1
"""
from django.db import migrations, models
import django.db.models.deletion


def backfill_remaining_null_tenant(apps, schema_editor):
    Exam = apps.get_model("exams", "Exam")
    Tenant = apps.get_model("core", "Tenant")

    null_exams = Exam.objects.filter(tenant__isnull=True)
    count = null_exams.count()
    if count == 0:
        return

    # Tenant 1 = dev/test tenant (per CLAUDE.md)
    tenant_1 = Tenant.objects.filter(id=1).first()
    if not tenant_1:
        raise Exception(
            f"Cannot backfill {count} null-tenant exams: Tenant 1 does not exist. "
            "Manual intervention required."
        )

    null_exams.update(tenant_id=1)


class Migration(migrations.Migration):

    dependencies = [
        ("exams", "0012_alter_questionexplanation_id"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill_remaining_null_tenant, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="exam",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="exams",
                to="core.tenant",
                help_text="이 시험이 속한 학원. template exam의 tenant isolation 보장.",
            ),
        ),
    ]
