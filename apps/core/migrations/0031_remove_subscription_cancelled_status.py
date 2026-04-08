"""
Program.subscription_status에서 'cancelled' 선택지 제거.

cancelled → active (cancel_at_period_end=True) 또는 expired로 마이그레이션.
해지 예약은 cancel_at_period_end 플래그로 관리하며,
subscription_status는 서비스 이용 가능 상태(active/grace/expired)만 나타낸다.

롤백: cancelled를 다시 choices에 추가하면 됨. 데이터 손실 없음 (canceled_at 보존).
"""

from django.db import migrations, models


def migrate_cancelled_to_flag(apps, schema_editor):
    """cancelled 상태인 Program을 active+cancel_at_period_end 또는 expired로 변환"""
    Program = apps.get_model("core", "Program")
    from datetime import date

    today = date.today()
    for program in Program.objects.filter(subscription_status="cancelled"):
        if program.subscription_expires_at and program.subscription_expires_at < today:
            # 이미 만료됨
            program.subscription_status = "expired"
        else:
            # 아직 이용 가능 기간 → active + 해지 예약
            program.subscription_status = "active"
            program.cancel_at_period_end = True
        program.save(update_fields=["subscription_status", "cancel_at_period_end"])


def reverse_migration(apps, schema_editor):
    """역방향: cancel_at_period_end=True인 active → cancelled로 복원"""
    Program = apps.get_model("core", "Program")
    Program.objects.filter(
        subscription_status="active",
        cancel_at_period_end=True,
    ).update(subscription_status="cancelled")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0030_set_dnb_school_level_mode"),
    ]

    operations = [
        # 1. 데이터 마이그레이션 먼저 (cancelled → active/expired)
        migrations.RunPython(migrate_cancelled_to_flag, reverse_migration),
        # 2. choices 변경 (cancelled 제거)
        migrations.AlterField(
            model_name="program",
            name="subscription_status",
            field=models.CharField(
                choices=[
                    ("active", "활성"),
                    ("expired", "만료"),
                    ("grace", "유예기간"),
                ],
                db_index=True,
                default="active",
                help_text="구독 상태",
                max_length=20,
            ),
        ),
    ]
