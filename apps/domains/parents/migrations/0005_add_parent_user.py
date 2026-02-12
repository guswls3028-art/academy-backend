# PATH: apps/domains/parents/migrations/0005_add_parent_user.py
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("parents", "0004_make_parent_tenant_not_null"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="parent",
            name="user",
            field=models.OneToOneField(
                blank=True,
                help_text="학부모 로그인 계정 (ID = 학부모 전화번호)",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="parent_profile",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
