"""LandingConsultRequest 모델 추가 — 학원 홈페이지 상담 요청 폼."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0035_workerheartbeatmodel"),
    ]

    operations = [
        migrations.CreateModel(
            name="LandingConsultRequest",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=50)),
                ("phone", models.CharField(max_length=20)),
                ("interest", models.CharField(blank=True, max_length=80)),
                ("message", models.TextField(blank=True)),
                ("source", models.CharField(default="landing", help_text="제출 출처(landing/reports/...)", max_length=40)),
                ("read_at", models.DateTimeField(blank=True, null=True)),
                ("admin_memo", models.TextField(blank=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        db_index=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="landing_consult_requests",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "core_landing_consult_request",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["tenant", "-created_at"], name="core_landin_tenant__c8a3a4_idx"),
                    models.Index(fields=["tenant", "read_at"], name="core_landin_tenant__a3b1c5_idx"),
                ],
            },
        ),
    ]
