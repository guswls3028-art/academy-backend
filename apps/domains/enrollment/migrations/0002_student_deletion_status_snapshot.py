from django.db import migrations, models


TRIGGER_NAME = "enrollment_preserve_student_deletion_status_trigger"
FUNCTION_NAME = "enrollment_preserve_student_deletion_status"


def preserve_legacy_deleted_students_fail_closed(apps, schema_editor):
    Enrollment = apps.get_model("enrollment", "Enrollment")
    Enrollment.objects.filter(
        student__deleted_at__isnull=False,
        status_before_student_deletion__isnull=True,
    ).update(
        status="INACTIVE",
        status_before_student_deletion="INACTIVE",
    )


def install_mixed_runtime_snapshot_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    enrollment_table = schema_editor.quote_name(
        apps.get_model("enrollment", "Enrollment")._meta.db_table
    )
    student_table = schema_editor.quote_name(
        apps.get_model("students", "Student")._meta.db_table
    )
    schema_editor.execute(
        f"""
        CREATE OR REPLACE FUNCTION {FUNCTION_NAME}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.status = 'INACTIVE'
               AND OLD.status IN ('ACTIVE', 'PENDING')
               AND NEW.status_before_student_deletion IS NULL
               AND EXISTS (
                   SELECT 1
                   FROM {student_table} student
                   WHERE student.id = NEW.student_id
                     AND student.deleted_at IS NOT NULL
               )
            THEN
                NEW.status_before_student_deletion := OLD.status;
            END IF;
            RETURN NEW;
        END;
        $$;

        DROP TRIGGER IF EXISTS {TRIGGER_NAME} ON {enrollment_table};
        CREATE TRIGGER {TRIGGER_NAME}
        BEFORE UPDATE OF status ON {enrollment_table}
        FOR EACH ROW
        EXECUTE FUNCTION {FUNCTION_NAME}();
        """
    )


def remove_mixed_runtime_snapshot_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    enrollment_table = schema_editor.quote_name(
        apps.get_model("enrollment", "Enrollment")._meta.db_table
    )
    schema_editor.execute(
        f"""
        DROP TRIGGER IF EXISTS {TRIGGER_NAME} ON {enrollment_table};
        DROP FUNCTION IF EXISTS {FUNCTION_NAME}();
        """
    )


class Migration(migrations.Migration):
    dependencies = [
        ("enrollment", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="enrollment",
            name="status_before_student_deletion",
            field=models.CharField(
                blank=True,
                choices=[
                    ("ACTIVE", "활성"),
                    ("INACTIVE", "비활성"),
                    ("PENDING", "대기"),
                ],
                editable=False,
                help_text=(
                    "학생 소프트 삭제 직전 수강 상태. 학생 복원 시 한 번 사용하고 "
                    "다시 비웁니다."
                ),
                max_length=20,
                null=True,
            ),
        ),
        migrations.RunPython(
            preserve_legacy_deleted_students_fail_closed,
            migrations.RunPython.noop,
        ),
        migrations.RunPython(
            install_mixed_runtime_snapshot_trigger,
            remove_mixed_runtime_snapshot_trigger,
        ),
    ]
