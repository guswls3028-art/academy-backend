from django.db import migrations, models


ACADEMY_MIGRATION_PHASE = "contract"
ACADEMY_MIGRATION_REASON = (
    "새 학원의 패스카드 자동 색상 기본값만 켜며 기존 학원의 저장된 선택은 보존한다."
)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0061_tenant_clinic_booking_interval_minutes_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tenant",
            name="clinic_use_daily_random",
            field=models.BooleanField(default=True, help_text="매일 자동 3색 사용 시 True"),
        ),
    ]
