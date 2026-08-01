from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("problem_studio", "0003_document_style_source_matching"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProblemStudioBetaRun",
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
                ("job_id", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("reserved", "진행 중"),
                            ("completed", "사용 완료"),
                            ("released", "차감 취소"),
                        ],
                        db_index=True,
                        default="reserved",
                        max_length=16,
                    ),
                ),
                ("release_reason", models.CharField(blank=True, default="", max_length=240)),
                (
                    "requested_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="problem_studio_beta_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="problem_studio_beta_runs",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "problem_studio_beta_run",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="problemstudiobetarun",
            constraint=models.UniqueConstraint(
                condition=~models.Q(job_id=""),
                fields=("job_id",),
                name="uq_ps_beta_run_job",
            ),
        ),
        migrations.AddIndex(
            model_name="problemstudiobetarun",
            index=models.Index(
                fields=["tenant", "status", "created_at"],
                name="idx_ps_beta_tenant_status",
            ),
        ),
    ]
