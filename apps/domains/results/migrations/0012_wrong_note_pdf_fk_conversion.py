# Convert WrongNotePDF integer references to ForeignKey fields.
# DB columns remain unchanged via db_column.

import django.db.models.deletion
from django.db import migrations, models


def cleanup_wrong_note_pdf_refs(apps, schema_editor):
    WrongNotePDF = apps.get_model("results", "WrongNotePDF")
    Enrollment = apps.get_model("enrollment", "Enrollment")
    Lecture = apps.get_model("lectures", "Lecture")
    Exam = apps.get_model("exams", "Exam")

    valid_enrollment_ids = set(Enrollment.objects.values_list("id", flat=True))
    valid_lecture_ids = set(Lecture.objects.values_list("id", flat=True))
    valid_exam_ids = set(Exam.objects.values_list("id", flat=True))

    invalid_enrollments = WrongNotePDF.objects.exclude(enrollment_id__in=valid_enrollment_ids)
    enrollment_count = invalid_enrollments.count()
    if enrollment_count:
        invalid_enrollments.delete()
        print(f"  Deleted {enrollment_count} WrongNotePDF rows with invalid enrollment_id")

    invalid_lectures = (
        WrongNotePDF.objects
        .exclude(lecture_id__isnull=True)
        .exclude(lecture_id__in=valid_lecture_ids)
    )
    lecture_count = invalid_lectures.update(lecture_id=None)
    if lecture_count:
        print(f"  Cleared {lecture_count} invalid WrongNotePDF lecture_id values")

    invalid_exams = (
        WrongNotePDF.objects
        .exclude(exam_id__isnull=True)
        .exclude(exam_id__in=valid_exam_ids)
    )
    exam_count = invalid_exams.update(exam_id=None)
    if exam_count:
        print(f"  Cleared {exam_count} invalid WrongNotePDF exam_id values")


class Migration(migrations.Migration):

    dependencies = [
        ("enrollment", "0001_initial"),
        ("exams", "0017_sheet_omr_shape"),
        ("lectures", "0007_session_regular_order_session_session_type_and_more"),
        ("results", "0011_score_edit_draft_fk_conversion"),
    ]

    operations = [
        migrations.RunPython(cleanup_wrong_note_pdf_refs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="wrongnotepdf",
            name="enrollment_id",
            field=models.ForeignKey(
                to="enrollment.Enrollment",
                on_delete=django.db.models.deletion.CASCADE,
                db_column="enrollment_id",
                related_name="wrong_note_pdf_jobs",
            ),
        ),
        migrations.RenameField(
            model_name="wrongnotepdf",
            old_name="enrollment_id",
            new_name="enrollment",
        ),
        migrations.AlterField(
            model_name="wrongnotepdf",
            name="lecture_id",
            field=models.ForeignKey(
                to="lectures.Lecture",
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                db_column="lecture_id",
                related_name="wrong_note_pdf_jobs",
            ),
        ),
        migrations.RenameField(
            model_name="wrongnotepdf",
            old_name="lecture_id",
            new_name="lecture",
        ),
        migrations.AlterField(
            model_name="wrongnotepdf",
            name="exam_id",
            field=models.ForeignKey(
                to="exams.Exam",
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                db_column="exam_id",
                related_name="wrong_note_pdf_jobs",
            ),
        ),
        migrations.RenameField(
            model_name="wrongnotepdf",
            old_name="exam_id",
            new_name="exam",
        ),
    ]
