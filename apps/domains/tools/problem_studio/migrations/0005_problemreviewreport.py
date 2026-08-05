from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("problem_studio", "0004_problemstudiobetarun"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProblemReviewReport",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("analysis_job_id", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("analyzing", "분석 중"),
                            ("draft", "검수 초안"),
                            ("failed", "분석 실패"),
                        ],
                        db_index=True,
                        default="analyzing",
                        max_length=16,
                    ),
                ),
                ("title", models.CharField(blank=True, default="", max_length=200)),
                ("source_name", models.CharField(blank=True, default="", max_length=255)),
                ("source_summary", models.JSONField(blank=True, default=dict)),
                ("draft", models.JSONField(blank=True, default=dict)),
                ("version", models.PositiveIntegerField(default=1)),
                ("last_error", models.TextField(blank=True, default="")),
                (
                    "requested_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="problem_review_reports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="problem_review_reports",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "problem_review_report",
                "ordering": ["-updated_at"],
                "constraints": [
                    models.UniqueConstraint(
                        condition=~models.Q(analysis_job_id=""),
                        fields=("analysis_job_id",),
                        name="uq_problem_review_analysis_job",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=["tenant", "requested_by", "updated_at"],
                        name="idx_problem_review_owner",
                    ),
                    models.Index(
                        fields=["tenant", "status", "updated_at"],
                        name="idx_problem_review_status",
                    ),
                ],
            },
        ),
    ]
