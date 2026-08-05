import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("landing_public", "0006_rename_lp_mshow_tn_st_pa_idx_landing_pub_tenant__33b2f5_idx_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PublicProblemReviewShowcase",
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
                ("report_id_ref", models.UUIDField(db_index=True)),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[("published", "공개"), ("hidden", "비공개")],
                        db_index=True,
                        default="published",
                        max_length=12,
                    ),
                ),
                ("published_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("snapshot", models.JSONField(blank=True, default=dict)),
                ("snapshot_pdf_key", models.CharField(blank=True, default="", max_length=512)),
                ("snapshot_pdf_bytes", models.PositiveIntegerField(default=0)),
                ("snapshot_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("view_count", models.PositiveIntegerField(default=0)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_public_problem_review_showcases",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="public_problem_review_showcases",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "landing_public_problem_review_showcase",
                "ordering": ["-published_at", "-created_at"],
                "indexes": [
                    models.Index(
                        fields=["tenant", "status", "-published_at"],
                        name="lp_problem_review_public_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("tenant", "report_id_ref"),
                        name="lp_problem_review_tenant_report_uniq",
                    ),
                ],
            },
        ),
    ]
