"""Dead 알림톡 trigger AutoSendConfig row data migration cleanup (2026-05-12).

목적: 학원장 백로그 #8 — 안 쓰이는 알림톡 템플릿/trigger 잔재 일괄 정리.

대상 5 trigger:
  - `clinic_check_out` — `clinic_self_study_completed` 로 통합되며 SSOT 제거
  - `urgent_notice` — 카카오 알림톡 정책 위반으로 제거
  - `class_enrollment_complete` — DISABLED (정책상 의미 없음)
  - `enrollment_expiring_soon` — DISABLED (미구현)
  - `student_signup` — DISABLED (레거시)

본 migration deploy 시 모든 환경(dev/staging/prod) 의 AutoSendConfig 에서 위 trigger
row 일괄 삭제. 재실행 안전(idempotent — 이미 빈 set이면 noop).

참고: management command `cleanup_dead_message_triggers` 와 동일 결과. command 는 ad-hoc
실행용, 본 migration 은 deploy 자동 적용용.
"""
from django.db import migrations


DEAD_TRIGGERS = [
    "clinic_check_out",
    "urgent_notice",
    "class_enrollment_complete",
    "enrollment_expiring_soon",
    "student_signup",
]


def cleanup_dead_rows(apps, schema_editor):
    """AutoSendConfig 에서 dead trigger row 삭제."""
    AutoSendConfig = apps.get_model("messaging", "AutoSendConfig")
    deleted, _ = AutoSendConfig.objects.filter(trigger__in=DEAD_TRIGGERS).delete()
    # 로그는 deploy 출력에 노출됨
    print(f"  [cleanup_dead_triggers] AutoSendConfig deleted: {deleted} rows")


def reverse_noop(apps, schema_editor):
    """역방향 noop — 삭제된 row 복원 X (학원장 설정값이 정확치 않음)."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0025_alter_autosendconfig_trigger"),
    ]

    operations = [
        migrations.RunPython(cleanup_dead_rows, reverse_noop),
    ]
