import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0050_platformpushoutbox"),
        ("enrollment", "0001_initial"),
        ("lectures", "0007_session_regular_order_session_session_type_and_more"),
        ("progress", "0012_cliniclink_source_removed_resolution"),
    ]

    operations = [
        migrations.CreateModel(
            name="AssessmentCorrection",
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
                    "source_type",
                    models.CharField(
                        choices=[("exam", "시험"), ("homework", "과제")],
                        max_length=20,
                    ),
                ),
                ("source_id", models.PositiveIntegerField()),
                ("completed", models.BooleanField(default=False)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "source_updated_at_snapshot",
                    models.DateTimeField(
                        blank=True,
                        help_text="완료 확인 당시 원본 점수의 updated_at. 점수가 바뀌면 완료 상태를 무효화한다.",
                        null=True,
                    ),
                ),
                (
                    "enrollment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assessment_corrections",
                        to="enrollment.enrollment",
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assessment_corrections",
                        to="lectures.session",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assessment_corrections",
                        to="core.tenant",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_assessment_corrections",
                        to="core.user",
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["session", "enrollment"],
                        name="progress_ac_session_enroll_idx",
                    ),
                    models.Index(
                        fields=["tenant", "completed"],
                        name="progress_ac_tenant_done_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "tenant",
                            "enrollment",
                            "session",
                            "source_type",
                            "source_id",
                        ),
                        name="uniq_assess_correction_source",
                    ),
                ],
            },
        ),
    ]
