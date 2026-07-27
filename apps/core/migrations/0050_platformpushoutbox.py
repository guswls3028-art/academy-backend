from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0049_platforminboxincidentstate"),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformPushOutbox",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("contact", "Contact"),
                            ("bug", "Bug"),
                            ("feedback", "Feedback"),
                            ("incident", "Incident"),
                        ],
                        max_length=16,
                    ),
                ),
                ("item_id", models.PositiveBigIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("processing", "Processing"),
                            ("sent", "Sent"),
                            ("dead", "Dead"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                (
                    "next_attempt_at",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                    ),
                ),
                ("locked_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, default="", max_length=255)),
            ],
            options={
                "db_table": "platform_push_outbox",
                "ordering": ["created_at"],
                "indexes": [
                    models.Index(
                        fields=["status", "next_attempt_at"],
                        name="platform_push_due_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("kind", "item_id"),
                        name="uniq_platform_push_item",
                    ),
                ],
            },
        ),
    ]
