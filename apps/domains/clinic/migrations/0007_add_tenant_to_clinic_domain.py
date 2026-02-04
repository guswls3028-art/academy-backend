# PATH: apps/domains/clinic/migrations/0007_add_tenant_to_clinic_domain.py

from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


def forwards_fill_tenant(apps, schema_editor):
    """
    기존 clinic 데이터 tenant 백필
    """
    Tenant = apps.get_model("core", "Tenant")

    default_tenant_id = getattr(settings, "DEFAULT_TENANT_ID", None)

    if default_tenant_id:
        tenant = Tenant.objects.filter(id=default_tenant_id).first()
    else:
        tenant = Tenant.objects.order_by("id").first()

    if tenant is None:
        raise RuntimeError("Tenant not found. Cannot backfill clinic.tenant")

    Session = apps.get_model("clinic", "Session")
    SessionParticipant = apps.get_model("clinic", "SessionParticipant")
    Test = apps.get_model("clinic", "Test")
    Submission = apps.get_model("clinic", "Submission")

    Session.objects.filter(tenant__isnull=True).update(tenant=tenant)
    SessionParticipant.objects.filter(tenant__isnull=True).update(tenant=tenant)
    Test.objects.filter(tenant__isnull=True).update(tenant=tenant)
    Submission.objects.filter(tenant__isnull=True).update(tenant=tenant)


class Migration(migrations.Migration):

    dependencies = [
        ("clinic", "0006_alter_session_created_by_and_more"),
        ("core", "0001_initial"),
    ]

    operations = [
        # -------------------------------------------------
        # 1. tenant 필드 추가 (NULL 허용)
        # -------------------------------------------------
        migrations.AddField(
            model_name="session",
            name="tenant",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="clinic_sessions",
                to="core.tenant",
            ),
        ),
        migrations.AddField(
            model_name="sessionparticipant",
            name="tenant",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="clinic_participants",
                to="core.tenant",
            ),
        ),
        migrations.AddField(
            model_name="test",
            name="tenant",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="clinic_tests",
                to="core.tenant",
            ),
        ),
        migrations.AddField(
            model_name="submission",
            name="tenant",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="clinic_submissions",
                to="core.tenant",
            ),
        ),

        # -------------------------------------------------
        # 2. tenant 데이터 백필
        # -------------------------------------------------
        migrations.RunPython(forwards_fill_tenant, migrations.RunPython.noop),

        # -------------------------------------------------
        # 3. tenant NOT NULL 전환
        # -------------------------------------------------
        migrations.AlterField(
            model_name="session",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="clinic_sessions",
                to="core.tenant",
            ),
        ),
        migrations.AlterField(
            model_name="sessionparticipant",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="clinic_participants",
                to="core.tenant",
            ),
        ),
        migrations.AlterField(
            model_name="test",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="clinic_tests",
                to="core.tenant",
            ),
        ),
        migrations.AlterField(
            model_name="submission",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="clinic_submissions",
                to="core.tenant",
            ),
        ),

        # -------------------------------------------------
        # 4. tenant 포함 신규 제약만 추가 (기존 제약 제거 ❌)
        # -------------------------------------------------
        migrations.AddConstraint(
            model_name="session",
            constraint=models.UniqueConstraint(
                fields=["tenant", "date", "start_time", "location"],
                name="uniq_clinic_session_per_tenant_time_location",
            ),
        ),
        migrations.AddConstraint(
            model_name="sessionparticipant",
            constraint=models.UniqueConstraint(
                fields=["tenant", "session", "student"],
                name="uniq_clinic_participant_per_tenant",
            ),
        ),
        migrations.AddConstraint(
            model_name="test",
            constraint=models.UniqueConstraint(
                fields=["tenant", "session", "round"],
                name="uniq_clinic_test_per_tenant_session_round",
            ),
        ),
        migrations.AddConstraint(
            model_name="submission",
            constraint=models.UniqueConstraint(
                fields=["tenant", "test", "student"],
                name="uniq_clinic_submission_per_tenant",
            ),
        ),
    ]
