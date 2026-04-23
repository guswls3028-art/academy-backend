"""
ClinicLink resolution lifecycle 개선:
1) resolution_history JSONField 추가 (append-only 이력 보존)
2) ResolutionType choices에 CARRIED_OVER 추가 ("다음 차수로 이월")
3) 기존 WAIVED + resolution_evidence.carried_over=True 레코드를 CARRIED_OVER로 backfill

⚠️ 운영 노트 (배포 후 추가됨 — 2026-04-23):

[적용/성능]
- AddField(JSONField, default=list)는 PostgreSQL 12+ 에서 metadata-only로 처리돼 table
  rewrite 없이 즉시 완료. 대형 테넌트에서도 lock 시간 짧음.
- AlterField(choices=...)는 Python/ORM 레벨 전용으로 SQL 변경 없음.
- RunPython backfill_carried_over 의 UPDATE는 (resolution_type='WAIVED') 조건 매칭
  row 전체에 row-level lock. resolution_type 에 인덱스가 없으면 대형 테넌트에서
  수십초 대기 가능 — 배포 시 저트래픽 시간대 권장.

[롤백 주의]
- reverse_backfill_carried_over 는 **CARRIED_OVER → WAIVED 전량 되돌리기** 이므로,
  이 마이그레이션 적용 이후 ClinicResolutionService.carry_over() 로 정상 생성된
  "진짜 CARRIED_OVER" 레코드도 WAIVED 로 잘못 되돌아간다. evidence.carried_over
  마커가 양쪽 모두에 있어서 forward backfill된 것과 이후 정상 생성된 것을 구분할
  방법이 없다.
- 따라서 0009 로 되돌릴 필요가 있다면, 먼저 CARRIED_OVER 분포를 사전 조사하고
  필요 시 임시 마커(evidence["_pre_0010"]=True)를 추가해 보존한 뒤 수동 SQL 로
  역마이그레이션을 수행해야 한다. `python manage.py detect_clinic_drift` 로 현재
  분포 확인 가능.

[감사]
- RunPython update 는 resolution_history 에 append 하지 않으므로 언제 backfill
  되었는지 DB 에 기록되지 않는다. 운영 조사 시 본 마이그레이션 적용일을
  CI 이력 / settings 배포 로그에서 찾아야 한다.
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
