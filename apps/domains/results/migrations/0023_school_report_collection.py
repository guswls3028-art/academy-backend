from django.db import migrations, models


ACADEMY_MIGRATION_PHASE = "contract"
ACADEMY_MIGRATION_REASON = (
    "Widen the reported-score evidence check for explicitly unverified direct school entries "
    "while retaining existing file-backed write and verification rules."
)


def limit_ddl_wait(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")


class Migration(migrations.Migration):
    dependencies = [("results", "0022_examattempt_current_fact_after_id")]

    operations = [
        migrations.RunPython(limit_ddl_wait, migrations.RunPython.noop),
        migrations.AddField(
            model_name="studentreportedscore",
            name="direct_entry",
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="studentreportedscore",
                    name="status",
                    field=models.CharField(
                        max_length=12,
                        choices=[
                            ("pending", "검토 대기"),
                            ("verified", "확인 완료"),
                            ("rejected", "반려"),
                            ("voided", "통계 제외"),
                            ("self_entered", "직접 입력"),
                            ("self_removed", "직접 입력 수정으로 제외"),
                        ],
                        default="pending",
                        db_index=True,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="studentreportedscore",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(evidence_file__isnull=False)
                    | models.Q(status__in=["rejected", "voided"])
                    | models.Q(
                        direct_entry=True,
                        status__in=["self_entered", "self_removed"],
                        source="school_exam",
                        semester__isnull=False,
                        semester__in=[1, 2],
                        exam_round__in=["first", "second"],
                    )
                ),
                name="reported_score_active_evidence_or_direct",
            ),
        ),
        migrations.AddConstraint(
            model_name="studentreportedscore",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(direct_entry=False) & ~models.Q(status__in=["self_entered", "self_removed"])
                    | models.Q(
                        direct_entry=True,
                        evidence_file__isnull=True,
                        status__in=["self_entered", "self_removed"],
                    )
                ),
                name="reported_score_direct_entry_valid",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="studentreportedscore",
            name="reported_score_active_requires_evidence",
        ),
        migrations.RunPython(migrations.RunPython.noop, limit_ddl_wait),
    ]
