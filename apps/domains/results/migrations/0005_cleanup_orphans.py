# Hand-written data migration: Clean up orphan rows before FK conversion
# ExamAttempt.exam_id, ResultItem.question_id, Result.target_id orphans

from django.db import migrations


def cleanup_orphans(apps, schema_editor):
    """
    Delete rows referencing deleted exams/questions.
    Must run before FK constraints are added.
    """
    ExamAttempt = apps.get_model("results", "ExamAttempt")
    Exam = apps.get_model("exams", "Exam")
    ResultItem = apps.get_model("results", "ResultItem")
    ExamQuestion = apps.get_model("exams", "ExamQuestion")
    Result = apps.get_model("results", "Result")

    # 1. ExamAttempt orphans (exam_id references deleted exams)
    valid_exam_ids = set(Exam.objects.values_list("id", flat=True))
    attempt_orphans = ExamAttempt.objects.exclude(exam_id__in=valid_exam_ids)
    count_attempts = attempt_orphans.count()
    if count_attempts:
        attempt_orphans.delete()
        print(f"  Deleted {count_attempts} orphan ExamAttempt rows")

    # 2. ResultItem orphans (question_id references deleted questions)
    valid_question_ids = set(ExamQuestion.objects.values_list("id", flat=True))
    item_orphans = ResultItem.objects.exclude(question_id__in=valid_question_ids)
    count_items = item_orphans.count()
    if count_items:
        item_orphans.delete()
        print(f"  Deleted {count_items} orphan ResultItem rows")

    # 3. Result orphans (target_type=exam, target_id references deleted exams)
    result_orphans = Result.objects.filter(
        target_type="exam"
    ).exclude(target_id__in=valid_exam_ids)
    count_results = result_orphans.count()
    if count_results:
        result_orphans.delete()
        print(f"  Deleted {count_results} orphan Result rows")


class Migration(migrations.Migration):

    dependencies = [
        ("results", "0004_score_edit_draft_tenant_unique"),
        ("exams", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(cleanup_orphans, migrations.RunPython.noop),
    ]
