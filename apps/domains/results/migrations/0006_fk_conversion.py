# Hand-written migration: Convert integer FK fields to actual ForeignKey fields
# DB columns remain unchanged (db_column= preserves original name)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("results", "0005_cleanup_orphans"),
        ("enrollment", "0001_initial"),
        ("exams", "0001_initial"),
    ]

    operations = [
        # =============================================
        # 1. ExamAttempt: exam_id → exam FK, enrollment_id → enrollment FK
        # =============================================

        # Remove old unique_together before field changes
        migrations.AlterUniqueTogether(
            name="examattempt",
            unique_together=set(),
        ),

        migrations.AlterField(
            model_name="examattempt",
            name="exam_id",
            field=models.ForeignKey(
                to="exams.Exam",
                on_delete=django.db.models.deletion.CASCADE,
                db_column="exam_id",
                related_name="attempts",
            ),
        ),
        migrations.RenameField(
            model_name="examattempt",
            old_name="exam_id",
            new_name="exam",
        ),

        migrations.AlterField(
            model_name="examattempt",
            name="enrollment_id",
            field=models.ForeignKey(
                to="enrollment.Enrollment",
                on_delete=django.db.models.deletion.CASCADE,
                db_column="enrollment_id",
                related_name="exam_attempts",
            ),
        ),
        migrations.RenameField(
            model_name="examattempt",
            old_name="enrollment_id",
            new_name="enrollment",
        ),

        # Re-add unique_together with new field names
        migrations.AlterUniqueTogether(
            name="examattempt",
            unique_together={("exam", "enrollment", "attempt_index")},
        ),

        # =============================================
        # 2. Result: enrollment_id → enrollment FK, attempt_id → attempt FK
        # =============================================

        # Remove old unique_together
        migrations.AlterUniqueTogether(
            name="result",
            unique_together=set(),
        ),

        migrations.AlterField(
            model_name="result",
            name="enrollment_id",
            field=models.ForeignKey(
                to="enrollment.Enrollment",
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                db_column="enrollment_id",
                related_name="results",
            ),
        ),
        migrations.RenameField(
            model_name="result",
            old_name="enrollment_id",
            new_name="enrollment",
        ),

        migrations.AlterField(
            model_name="result",
            name="attempt_id",
            field=models.ForeignKey(
                to="results.ExamAttempt",
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                db_column="attempt_id",
                db_index=True,
                related_name="results",
                help_text="이 Result가 참조하는 대표 ExamAttempt.id",
            ),
        ),
        migrations.RenameField(
            model_name="result",
            old_name="attempt_id",
            new_name="attempt",
        ),

        # Re-add unique_together
        migrations.AlterUniqueTogether(
            name="result",
            unique_together={("target_type", "target_id", "enrollment")},
        ),

        # =============================================
        # 3. ResultFact: enrollment_id → enrollment FK, attempt_id → attempt FK
        # =============================================

        migrations.AlterField(
            model_name="resultfact",
            name="enrollment_id",
            field=models.ForeignKey(
                to="enrollment.Enrollment",
                on_delete=django.db.models.deletion.CASCADE,
                db_column="enrollment_id",
                related_name="result_facts",
            ),
        ),
        migrations.RenameField(
            model_name="resultfact",
            old_name="enrollment_id",
            new_name="enrollment",
        ),

        migrations.AlterField(
            model_name="resultfact",
            name="attempt_id",
            field=models.ForeignKey(
                to="results.ExamAttempt",
                on_delete=django.db.models.deletion.CASCADE,
                null=True,
                blank=True,
                db_column="attempt_id",
                db_index=True,
                related_name="result_facts",
                help_text="이 Fact를 생성한 ExamAttempt.id",
            ),
        ),
        migrations.RenameField(
            model_name="resultfact",
            old_name="attempt_id",
            new_name="attempt",
        ),

        # =============================================
        # 4. ResultItem: question_id → question FK
        # =============================================

        # Remove old unique_together
        migrations.AlterUniqueTogether(
            name="resultitem",
            unique_together=set(),
        ),

        migrations.AlterField(
            model_name="resultitem",
            name="question_id",
            field=models.ForeignKey(
                to="exams.ExamQuestion",
                on_delete=django.db.models.deletion.CASCADE,
                db_column="question_id",
                related_name="result_items",
            ),
        ),
        migrations.RenameField(
            model_name="resultitem",
            old_name="question_id",
            new_name="question",
        ),

        # Re-add unique_together
        migrations.AlterUniqueTogether(
            name="resultitem",
            unique_together={("result", "question")},
        ),
    ]
