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
                    "stage",
                    models.CharField(
                        choices=[
                            ("extract", "문항 분석"),
                            ("solve", "정답·해설 생성"),
                            ("verify", "독립 검산"),
                            ("build", "PDF 생성"),
                            ("done", "완료"),
                        ],
                        db_index=True,
                        default="extract",
                        max_length=16,
                    ),
                ),
                ("source_name", models.CharField(blank=True, default="", max_length=255)),
                ("source_archive_key", models.CharField(blank=True, default="", max_length=512)),
                ("checkpoint_key", models.CharField(blank=True, default="", max_length=512)),
                ("solutions_key", models.CharField(blank=True, default="", max_length=512)),
                ("result_key", models.CharField(blank=True, default="", max_length=512)),
                ("result_filename", models.CharField(blank=True, default="", max_length=255)),
                ("request_payload", models.JSONField(blank=True, default=dict)),
                ("result_payload", models.JSONField(blank=True, default=dict)),
                ("question_count", models.PositiveIntegerField(default=0)),
                ("completed_question_count", models.PositiveIntegerField(default=0)),
                ("verified_question_count", models.PositiveIntegerField(default=0)),
                ("review_required_count", models.PositiveIntegerField(default=0)),
                ("last_error", models.TextField(blank=True, default="")),
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
                "constraints": [
                    models.UniqueConstraint(
                        condition=~models.Q(job_id=""),
                        fields=("job_id",),
                        name="uq_ps_beta_run_job",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=["tenant", "status", "created_at"],
                        name="idx_ps_beta_tenant_status",
                    ),
                    models.Index(
                        fields=["tenant", "requested_by", "created_at"],
                        name="idx_ps_beta_owner_created",
                    ),
                ],
            },
        ),
    ]
