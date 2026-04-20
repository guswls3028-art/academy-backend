"""
ClinicLink resolution lifecycle 개선:
1) resolution_history JSONField 추가 (append-only 이력 보존)
2) ResolutionType choices에 CARRIED_OVER 추가 ("다음 차수로 이월")
3) 기존 WAIVED + resolution_evidence.carried_over=True 레코드를 CARRIED_OVER로 backfill
"""
from django.db import migrations, models


def backfill_carried_over(apps, schema_editor):
    """
    기존 carry_over로 처리된 레코드(WAIVED + evidence.carried_over=True)를
    CARRIED_OVER로 재분류. WAIVED 통계에서 이월 건수 제거.
    """
    ClinicLink = apps.get_model("progress", "ClinicLink")
    updated = 0
    # PostgreSQL JSONB lookup 사용 — evidence.carried_over=True인 WAIVED 레코드
    qs = ClinicLink.objects.filter(
        resolution_type="WAIVED",
        resolution_evidence__carried_over=True,
    )
    updated = qs.update(resolution_type="CARRIED_OVER")
    if updated:
        print(f"  Backfilled {updated} ClinicLink rows from WAIVED(carried_over) to CARRIED_OVER")


def reverse_backfill_carried_over(apps, schema_editor):
    """CARRIED_OVER → WAIVED 되돌리기 (rollback)."""
    ClinicLink = apps.get_model("progress", "ClinicLink")
    ClinicLink.objects.filter(resolution_type="CARRIED_OVER").update(resolution_type="WAIVED")


class Migration(migrations.Migration):

    dependencies = [
        ("progress", "0009_cliniclink_tenant_not_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="cliniclink",
            name="resolution_history",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="해소/복원 전이 이력(append-only): 이전 evidence 및 액션 기록",
            ),
        ),
        migrations.AlterField(
            model_name="cliniclink",
            name="resolution_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("EXAM_PASS", "시험 통과"),
                    ("HOMEWORK_PASS", "과제 통과"),
                    ("MANUAL_OVERRIDE", "관리자 수동 해소"),
                    ("WAIVED", "면제"),
                    ("CARRIED_OVER", "다음 차수로 이월"),
                    ("BOOKING_LEGACY", "레거시(예약 기반)"),
                ],
                help_text="해소 유형: 시험통과/과제통과/수동해소/면제/레거시",
                max_length=30,
                null=True,
            ),
        ),
        migrations.RunPython(backfill_carried_over, reverse_backfill_carried_over),
    ]
