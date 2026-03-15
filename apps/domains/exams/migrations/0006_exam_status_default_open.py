# Generated migration: change default status from DRAFT to OPEN
# and convert existing DRAFT regular exams to OPEN.

from django.db import migrations, models


def convert_draft_to_open(apps, schema_editor):
    Exam = apps.get_model("exams", "Exam")
    # Regular 시험만 DRAFT→OPEN. 템플릿은 학생에게 노출 안 되므로 영향 없음.
    Exam.objects.filter(status="DRAFT", exam_type="regular").update(status="OPEN")
    # 템플릿도 일괄 OPEN 전환 (status가 의미 없으므로 통일)
    Exam.objects.filter(status="DRAFT", exam_type="template").update(status="OPEN")


class Migration(migrations.Migration):

    dependencies = [
        ("exams", "0005_exam_max_score_display_order"),
    ]

    operations = [
        migrations.AlterField(
            model_name="exam",
            name="status",
            field=models.CharField(
                choices=[("DRAFT", "초안"), ("OPEN", "진행중"), ("CLOSED", "마감")],
                db_index=True,
                default="OPEN",
                help_text="생성=OPEN(즉시 진행), 마감=CLOSED. DRAFT는 레거시(기존 데이터 호환).",
                max_length=20,
            ),
        ),
        migrations.RunPython(convert_draft_to_open, migrations.RunPython.noop),
    ]
