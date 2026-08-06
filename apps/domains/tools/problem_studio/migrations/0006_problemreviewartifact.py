from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("problem_studio", "0005_problemreviewreport"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProblemReviewArtifact",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
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
                ("job_id", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                (
                    "output_format",
                    models.CharField(choices=[("pdf", "PDF"), ("pptx", "PPTX")], max_length=8),
                ),
                ("report_version", models.PositiveIntegerField()),
                ("source_fingerprint", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "생성 중"),
                            ("ready", "다운로드 가능"),
                            ("failed", "생성 실패"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=12,
                    ),
                ),
                ("filename", models.CharField(blank=True, default="", max_length=255)),
                ("content_type", models.CharField(blank=True, default="", max_length=160)),
                ("size_bytes", models.PositiveBigIntegerField(default=0)),
                ("sha256", models.CharField(blank=True, default="", max_length=64)),
                ("r2_key", models.CharField(blank=True, default="", max_length=700)),
                ("error_message", models.TextField(blank=True, default="")),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="problem_review_artifacts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "report",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="artifacts",
                        to="problem_studio.problemreviewreport",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="problem_review_artifacts",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "problem_review_artifact",
                "ordering": ["-created_at"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("report", "report_version", "output_format", "source_fingerprint"),
                        name="uq_problem_review_artifact_snapshot",
                    ),
                    models.UniqueConstraint(
                        condition=~models.Q(job_id=""),
                        fields=("job_id",),
                        name="uq_problem_review_artifact_job",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=["tenant", "created_by", "created_at"],
                        name="idx_problem_review_art_owner",
                    ),
                    models.Index(
                        fields=["report", "status", "created_at"],
                        name="idx_problem_review_art_status",
                    ),
                ],
            },
        ),
    ]
