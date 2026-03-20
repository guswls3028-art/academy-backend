# ClinicLink remediation model extension
# Adds resolution_type, resolution_evidence, cycle_no fields
# Backfills existing resolved records as BOOKING_LEGACY
from django.db import migrations, models


def backfill_legacy_resolution_type(apps, schema_editor):
    """
    기존에 resolved_at이 설정된 ClinicLink를 BOOKING_LEGACY로 표시.
    운영 데이터 보존: 기존 resolved_at 값은 변경하지 않음.
    """
    ClinicLink = apps.get_model("progress", "ClinicLink")
    ClinicLink.objects.filter(
        resolved_at__isnull=False,
        resolution_type__isnull=True,
    ).update(resolution_type="BOOKING_LEGACY")


def reverse_backfill(apps, schema_editor):
    ClinicLink = apps.get_model("progress", "ClinicLink")
    ClinicLink.objects.filter(resolution_type="BOOKING_LEGACY").update(resolution_type=None)


class Migration(migrations.Migration):
    dependencies = [
        ("progress", "0003_fk_cliniclink_risklog"),
    ]

    operations = [
        migrations.AddField(
            model_name="cliniclink",
            name="resolution_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("EXAM_PASS", "시험 통과"),
                    ("HOMEWORK_PASS", "과제 통과"),
                    ("MANUAL_OVERRIDE", "관리자 수동 해소"),
                    ("WAIVED", "면제"),
                    ("BOOKING_LEGACY", "레거시(예약 기반)"),
                ],
                help_text="해소 유형: 시험통과/과제통과/수동해소/면제/레거시",
                max_length=30,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="cliniclink",
            name="resolution_evidence",
            field=models.JSONField(
                blank=True,
                help_text="해소 근거: {exam_id, attempt_id, homework_id, score, ...}",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="cliniclink",
            name="cycle_no",
            field=models.PositiveIntegerField(
                default=1,
                help_text="클리닉 차수: 1차, 2차, 3차...",
            ),
        ),
        migrations.AddIndex(
            model_name="cliniclink",
            index=models.Index(
                fields=["resolution_type"],
                name="progress_cl_resolut_idx",
            ),
        ),
        # Backfill existing resolved records
        migrations.RunPython(backfill_legacy_resolution_type, reverse_backfill),
    ]
