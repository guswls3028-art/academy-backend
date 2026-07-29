from django.db import migrations, models
from django.utils import timezone


def mark_existing_users_complete(apps, schema_editor):
    User = apps.get_model("core", "User")
    User.objects.filter(first_login_guide_completed_at__isnull=True).update(
        first_login_guide_completed_at=timezone.now()
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0051_product_usage_analytics"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="first_login_guide_completed_at",
            field=models.DateTimeField(
                blank=True,
                help_text="첫 접속 계정 안내를 확인한 시각. null이면 안내 대상.",
                null=True,
            ),
        ),
        migrations.RunPython(mark_existing_users_complete, migrations.RunPython.noop),
    ]
