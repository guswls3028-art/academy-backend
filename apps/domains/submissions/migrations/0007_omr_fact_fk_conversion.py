import django.db.models.deletion
from django.db import migrations, models


def normalize_omr_fact_ids(apps, schema_editor):
    OMRDetectedAnswer = apps.get_model("submissions", "OMRDetectedAnswer")
    OMRStudentMatch = apps.get_model("submissions", "OMRStudentMatch")
    Enrollment = apps.get_model("enrollment", "Enrollment")
    Exam = apps.get_model("exams", "Exam")
    Sheet = apps.get_model("exams", "Sheet")
    ExamQuestion = apps.get_model("exams", "ExamQuestion")
    db_alias = schema_editor.connection.alias

    valid_enrollment_ids = Enrollment.objects.using(db_alias).values("id")
    (
        OMRStudentMatch.objects.using(db_alias)
        .exclude(enrollment_id__isnull=True)
        .exclude(enrollment_id__in=valid_enrollment_ids)
        .update(enrollment_id=None)
    )

    structure_exam_cache = {}
    question_id_cache = {}

    def effective_structure_exam_id(exam_id):
        if exam_id in structure_exam_cache:
            return structure_exam_cache[exam_id]

        exam = (
            Exam.objects.using(db_alias)
            .filter(pk=exam_id)
            .only("id", "exam_type", "template_exam_id")
            .first()
        )
        if exam is None:
            structure_exam_cache[exam_id] = None
            return None

        if exam.exam_type == "template":
            structure_exam_cache[exam_id] = int(exam.id)
            return int(exam.id)

        has_own_sheet = Sheet.objects.using(db_alias).filter(exam_id=exam.id).exists()
        if has_own_sheet:
            structure_exam_cache[exam_id] = int(exam.id)
            return int(exam.id)

        if exam.template_exam_id:
            structure_exam_cache[exam_id] = int(exam.template_exam_id)
            return int(exam.template_exam_id)

        structure_exam_cache[exam_id] = int(exam.id)
        return int(exam.id)

    def expected_question_id(submission, question_number):
        if not submission or submission.target_type != "exam" or not submission.target_id:
            return None
        if not question_number:
            return None

        structure_exam_id = effective_structure_exam_id(int(submission.target_id))
        if structure_exam_id is None:
            return None

        cache_key = (structure_exam_id, int(question_number))
        if cache_key not in question_id_cache:
            question = (
                ExamQuestion.objects.using(db_alias)
                .filter(sheet__exam_id=structure_exam_id, number=int(question_number))
                .only("id")
                .first()
            )
            question_id_cache[cache_key] = int(question.id) if question else None
        return question_id_cache[cache_key]

    detected_answers = (
        OMRDetectedAnswer.objects.using(db_alias)
        .exclude(exam_question_id__isnull=True)
        .select_related("submission")
        .only("id", "exam_question_id", "question_number", "submission__target_type", "submission__target_id")
    )
    for detected in detected_answers.iterator(chunk_size=1000):
        expected_id = expected_question_id(detected.submission, detected.question_number)
        current_id = int(detected.exam_question_id) if detected.exam_question_id else None
        if current_id != expected_id:
            OMRDetectedAnswer.objects.using(db_alias).filter(pk=detected.pk).update(
                exam_question_id=expected_id
            )

    valid_question_ids = ExamQuestion.objects.using(db_alias).values("id")
    (
        OMRDetectedAnswer.objects.using(db_alias)
        .exclude(exam_question_id__isnull=True)
        .exclude(exam_question_id__in=valid_question_ids)
        .update(exam_question_id=None)
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("enrollment", "0001_initial"),
        ("exams", "0001_initial"),
        ("submissions", "0006_omrrecognitionrun_contract_snapshot"),
    ]

    operations = [
        migrations.RunPython(normalize_omr_fact_ids, noop_reverse),
        migrations.AlterField(
            model_name="omrdetectedanswer",
            name="exam_question_id",
            field=models.ForeignKey(
                blank=True,
                db_column="exam_question_id",
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="omr_detected_answers",
                to="exams.examquestion",
            ),
        ),
        migrations.RenameField(
            model_name="omrdetectedanswer",
            old_name="exam_question_id",
            new_name="exam_question",
        ),
        migrations.AlterField(
            model_name="omrstudentmatch",
            name="enrollment_id",
            field=models.ForeignKey(
                blank=True,
                db_column="enrollment_id",
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="omr_student_matches",
                to="enrollment.enrollment",
            ),
        ),
        migrations.RenameField(
            model_name="omrstudentmatch",
            old_name="enrollment_id",
            new_name="enrollment",
        ),
        migrations.RemoveIndex(
            model_name="omrstudentmatch",
            name="submissions_tenant__35d63f_idx",
        ),
        migrations.AddIndex(
            model_name="omrstudentmatch",
            index=models.Index(
                fields=["tenant", "enrollment", "matched_at"],
                name="submissions_tenant__35d63f_idx",
            ),
        ),
    ]
