from django.db import migrations, models


def infer_existing_grading_modes(apps, schema_editor):
    Exam = apps.get_model("exams", "Exam")
    Sheet = apps.get_model("exams", "Sheet")

    sheets = {
        int(row["exam_id"]): row
        for row in Sheet.objects.values(
            "exam_id",
            "choice_count",
            "essay_count",
        )
    }
    for exam in Exam.objects.all().iterator():
        sheet = sheets.get(int(exam.id))
        if sheet is None and exam.template_exam_id:
            sheet = sheets.get(int(exam.template_exam_id))
        if sheet is None:
            continue

        choice_count = int(sheet.get("choice_count") or 0)
        essay_count = int(sheet.get("essay_count") or 0)
        if choice_count > 0 and essay_count > 0:
            grading_mode = "mixed"
        elif essay_count > 0:
            grading_mode = "written"
        else:
            grading_mode = "choice"
        Exam.objects.filter(id=exam.id).update(
            grading_mode=grading_mode,
            choice_question_count=choice_count,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0018_examquestion_question_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="exam",
            name="grading_mode",
            field=models.CharField(
                choices=[
                    ("choice", "선택형"),
                    ("written", "답변형"),
                    ("mixed", "혼합형"),
                ],
                default="choice",
                help_text=(
                    "선택형은 OMR, 답변형은 수기 채점, "
                    "혼합형은 두 흐름을 함께 사용한다."
                ),
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="exam",
            name="manual_grading_method",
            field=models.CharField(
                choices=[
                    ("correctness", "정오 입력"),
                    ("score", "점수 입력"),
                ],
                default="score",
                help_text="답변형 문항을 문항별 점수 또는 정오로 입력하는 방식.",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="exam",
            name="choice_question_count",
            field=models.PositiveIntegerField(
                default=0,
                help_text="혼합형 시험에서 앞쪽 선택형 문항 수.",
            ),
        ),
        migrations.AddField(
            model_name="exam",
            name="segmentation_status",
            field=models.CharField(
                choices=[
                    ("none", "원본 없음"),
                    ("processing", "문항 분리 중"),
                    ("ready", "문항 분리 완료"),
                    ("failed", "문항 분리 실패"),
                    ("conversion_required", "PDF 변환 필요"),
                ],
                default="none",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="exam",
            name="source_filename",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="examasset",
            name="asset_type",
            field=models.CharField(
                choices=[
                    ("problem_pdf", "Problem PDF"),
                    ("problem_source", "Original problem source"),
                    ("omr_sheet", "OMR Sheet"),
                ],
                max_length=30,
            ),
        ),
        migrations.RunPython(
            infer_existing_grading_modes,
            migrations.RunPython.noop,
        ),
    ]
