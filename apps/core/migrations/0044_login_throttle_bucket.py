from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0043_set_ymath_no_section_mode"),
    ]

    operations = [
        migrations.CreateModel(
            name="LoginThrottleBucket",
            fields=[
                (
                    "bucket_key",
                    models.CharField(max_length=64, primary_key=True, serialize=False),
                ),
                (
                    "scope",
                    models.CharField(
                        choices=[("ip", "IP"), ("account", "Account")],
                        max_length=16,
                    ),
                ),
                ("request_count", models.PositiveIntegerField(default=0)),
                ("window_started_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "core_login_throttle_bucket"},
        ),
    ]
