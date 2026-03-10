# Generated migration: 휴식 시간 초 단위 저장 (실시간 시계 일시정지 반영)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("staffs", "0003_revert_staff_user_onetoone_tenant_isolation"),
    ]

    operations = [
        migrations.AddField(
            model_name="workrecord",
            name="break_total_seconds",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
