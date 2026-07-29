import apps.domains.students.models
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("students", "0014_clear_initial_password_plain"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="custom_fields",
            field=models.JSONField(
                blank=True,
                db_default={},
                default=dict,
                help_text="테넌트별 학생 사용자 정의 필드 값. 정의의 stable key를 사용한다.",
            ),
        ),
        migrations.CreateModel(
            name="StudentCustomFieldDefinition",
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
                    "key",
                    models.CharField(
                        default=apps.domains.students.models.generate_student_custom_field_key,
                        editable=False,
                        help_text="표시명 변경과 무관한 안정적인 학생 사용자 정의 필드 키",
                        max_length=32,
                    ),
                ),
                ("label", models.CharField(max_length=50)),
                (
                    "field_type",
                    models.CharField(
                        choices=[
                            ("text", "텍스트"),
                            ("number", "숫자"),
                            ("date", "날짜"),
                            ("select", "선택"),
                        ],
                        default="text",
                        max_length=12,
                    ),
                ),
                (
                    "aliases",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="Excel 헤더 호환 별칭. 이전 표시명도 유지한다.",
                    ),
                ),
                (
                    "options",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="select 타입에서 허용하는 값 목록",
                    ),
                ),
                ("position", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_student_custom_field_definitions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        db_index=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="student_custom_field_definitions",
                        to="core.tenant",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "id"],
                "indexes": [
                    models.Index(
                        fields=["tenant", "is_active", "position"],
                        name="students_st_tenant__4ffc23_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("tenant", "key"),
                        name="uniq_student_custom_field_key_per_tenant",
                    ),
                    models.UniqueConstraint(
                        fields=("tenant", "label"),
                        name="uniq_student_custom_field_label_per_tenant",
                    ),
                ],
            },
        ),
    ]
