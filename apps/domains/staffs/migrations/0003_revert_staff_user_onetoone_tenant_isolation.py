# 1테넌트 1프로그램: 도메인(테넌트)별 완전 격리. Staff.user = OneToOne 복원.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("staffs", "0002_staff_user_fk_per_tenant"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="staff",
            name="uniq_staff_user_per_tenant",
        ),
        migrations.AlterField(
            model_name="staff",
            name="user",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="staff_profile",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
