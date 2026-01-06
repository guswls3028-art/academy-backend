# apps/domains/submissions/migrations/0002_submissionanswer_contract_v2.py
from __future__ import annotations

from django.db import migrations, models


def forwards_copy_question_id_to_question_number(apps, schema_editor):
    """
    기존 question_id(혼재 가능)를 question_number로 보관해두는 migration.
    - exam_question_id는 worker 전환/백필로 채워지는 것이 정석
    """
    SubmissionAnswer = apps.get_model("submissions", "SubmissionAnswer")

    # 기존 컬럼이 있는 DB에서만 동작하도록 try 방어
    for sa in SubmissionAnswer.objects.all().iterator():
        # 이전 스키마에 question_id가 있었다면 그것을 number로 옮긴다.
        # (혼재일 수 있으나 "유실 방지" 목적)
        if hasattr(sa, "question_id") and getattr(sa, "question_id", None) is not None:
            sa.question_number = int(sa.question_id)
            sa.save(update_fields=["question_number"])


def backwards_restore_question_id(apps, schema_editor):
    """
    롤백 대비: question_number를 question_id로 복구
    """
    SubmissionAnswer = apps.get_model("submissions", "SubmissionAnswer")

    for sa in SubmissionAnswer.objects.all().iterator():
        if hasattr(sa, "question_number") and sa.question_number is not None:
            # backwards 시점에 question_id 컬럼이 있다고 가정
            if hasattr(sa, "question_id"):
                sa.question_id = int(sa.question_number)
                sa.save(update_fields=["question_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("submissions", "0001_initial"),
    ]

    operations = [
        # 1) question_number 추가
        migrations.AddField(
            model_name="submissionanswer",
            name="question_number",
            field=models.PositiveIntegerField(
                null=True,
                blank=True,
                help_text="Legacy migration only (number). Will be removed.",
            ),
        ),

        # 2) question_id -> question_number 데이터 보존
        migrations.RunPython(
            forwards_copy_question_id_to_question_number,
            backwards_restore_question_id,
        ),

        # 3) question_id 제거 (애매한 이름 제거가 핵심)
        migrations.RemoveField(
            model_name="submissionanswer",
            name="question_id",
        ),

        # 4) exam_question_id help_text/인덱스는 모델에서 유지
        #    (이미 존재하는 필드라면 여기서 AlterField 생략 가능)
    ]
