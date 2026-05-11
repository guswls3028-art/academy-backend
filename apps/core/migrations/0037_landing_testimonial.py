"""LandingTestimonialSubmission 모델 추가."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_landing_consult_request"),
    ]

    operations = [
        migrations.CreateModel(
            name="LandingTestimonialSubmission",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=50)),
                ("role", models.CharField(blank=True, help_text="학년/관계 등 (예: 고1 학부모)", max_length=80)),
                ("text", models.TextField()),
                ("status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")], db_index=True, default="pending", max_length=12)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True, null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_testimonials",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        db_index=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="landing_testimonials",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "core_landing_testimonial",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["tenant", "status", "-created_at"], name="core_landin_tenant__b9c4a2_idx"),
                ],
            },
        ),
    ]
