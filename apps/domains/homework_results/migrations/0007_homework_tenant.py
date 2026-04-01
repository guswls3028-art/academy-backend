"""
Add tenant FK to Homework model.

Homework had no direct tenant FK — tenant was inferred from session→lecture→tenant.
Templates (session=null) were completely unscoped.

Strategy:
1. Add nullable tenant FK
2. Backfill from session→lecture→tenant for regular homeworks
3. Assign orphans (no session) to tenant 1 (dev/test)
4. Enforce NOT NULL
"""
from django.db import migrations, models
import django.db.models.deletion


def backfill_homework_tenant(apps, schema_editor):
    Homework = apps.get_model("homework_results", "Homework")
    Session = apps.get_model("lectures", "Session")
    Tenant = apps.get_model("core", "Tenant")

    # 1. Regular homeworks with session
    for hw in Homework.objects.filter(tenant__isnull=True, session__isnull=False).select_related("session__lecture"):
        if hw.session and hw.session.lecture_id:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT tenant_id FROM lectures_lecture WHERE id = %s",
                    [hw.session.lecture_id],
                )
                row = cursor.fetchone()
                if row and row[0]:
                    hw.tenant_id = row[0]
                    hw.save(update_fields=["tenant_id"])

    # 2. Remaining (templates or orphans) → tenant 1
    remaining = Homework.objects.filter(tenant__isnull=True).count()
    if remaining > 0:
        tenant_1 = Tenant.objects.filter(id=1).first()
        if not tenant_1:
            raise Exception(
                f"Cannot backfill {remaining} null-tenant homeworks: Tenant 1 does not exist."
            )
        Homework.objects.filter(tenant__isnull=True).update(tenant_id=1)


class Migration(migrations.Migration):

    dependencies = [
        ("homework_results", "0006_homeworkscore_attempt_index_clinic_link"),
        ("core", "0001_initial"),
    ]

    operations = [
        # Step 1: Add nullable tenant FK
        migrations.AddField(
            model_name="homework",
            name="tenant",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="homeworks",
                to="core.tenant",
            ),
        ),
        # Step 2: Backfill
        migrations.RunPython(backfill_homework_tenant, migrations.RunPython.noop),
        # Step 3: Enforce NOT NULL
        migrations.AlterField(
            model_name="homework",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="homeworks",
                to="core.tenant",
                help_text="이 과제가 속한 학원.",
            ),
        ),
    ]
