# Generated manually for zero-downtime deployment (additive only)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("staffs", "0005_staff_profile_photo"),
    ]

    operations = [
        migrations.AddField(
            model_name="workrecord",
            name="meal_minutes",
            field=models.PositiveIntegerField(
                default=0,
                help_text="식사시간 (분). 근무시간에서 차감.",
            ),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="adjustment_amount",
            field=models.IntegerField(
                default=0,
                help_text="조정 금액 (양수=추가, 음수=차감). 자동 계산 후 가감.",
            ),
        ),
        migrations.AddField(
            model_name="workrecord",
            name="is_manually_edited",
            field=models.BooleanField(
                default=False,
                help_text="관리자가 근무시간/금액을 수동 수정했으면 True. 자동 재계산 방지.",
            ),
        ),
    ]
