"""
ClinicLink에 source_type/source_id 추가 — 시험/과제별 개별 추적

기존 ClinicLink는 (enrollment, session) 단위 → 다중 시험 세션에서 개별 추적 불가.
source_type/source_id 추가로 (enrollment, session, exam) 단위 추적 가능.

Backward compatible: 기존 데이터는 source_type/source_id = NULL로 유지.
Backfill: meta.exam_id가 있으면 source_type="exam", source_id=meta["exam_id"]로 설정.
"""
from django.db import migrations, models


def backfill_source_fields(apps, schema_editor):
    """기존 ClinicLink의 meta.exam_id를 source_type/source_id로 이동."""
    ClinicLink = apps.get_model("progress", "ClinicLink")
    updated = 0
    for link in ClinicLink.objects.filter(source_type__isnull=True).iterator():
        meta = link.meta or {}
        exam_id = meta.get("exam_id")
        if exam_id:
            link.source_type = "exam"
            link.source_id = int(exam_id)
            link.save(update_fields=["source_type", "source_id"])
            updated += 1
    if updated:
        print(f"  Backfilled {updated} ClinicLink rows with source_type/source_id from meta.exam_id")


class Migration(migrations.Migration):

    dependencies = [
        ("progress", "0004_cliniclink_resolution_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="cliniclink",
            name="source_type",
            field=models.CharField(
                blank=True,
                choices=[("exam", "시험"), ("homework", "과제")],
                help_text="출처 유형: exam 또는 homework",
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="cliniclink",
            name="source_id",
            field=models.IntegerField(
                blank=True,
                help_text="출처 ID: exam.id 또는 homework.id",
                null=True,
            ),
        ),
        migrations.AddIndex(
            model_name="cliniclink",
            index=models.Index(
                fields=["source_type", "source_id"],
                name="progress_cl_source__b9f3c7_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="cliniclink",
            constraint=models.UniqueConstraint(
                condition=models.Q(("source_type__isnull", False), ("source_id__isnull", False)),
                fields=["enrollment", "session", "source_type", "source_id", "cycle_no"],
                name="uniq_cliniclink_per_source_cycle",
            ),
        ),
        migrations.RunPython(backfill_source_fields, migrations.RunPython.noop),
    ]
