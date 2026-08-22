import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("students", "0017_student_pending_account_notice_origin_id_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StudentSupportSession",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                (
                    "end_reason",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("manual", "학생 화면에서 종료"),
                            ("window_closed", "지원 창 닫힘"),
                        ],
                        default="",
                        max_length=24,
                    ),
                ),
                (
                    "operator",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="support_sessions",
                        to="students.student",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="student_support_sessions",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "student_support_session",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="studentsupportsession",
            index=models.Index(
                fields=["tenant", "student", "-created_at"],
                name="stu_sup_student_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="studentsupportsession",
            index=models.Index(
                fields=["operator", "expires_at"],
                name="stu_sup_operator_exp_idx",
            ),
        ),
    ]
