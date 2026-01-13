# apps/domains/progress/migrations/0002_exam_aggregate_fields.py

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("progress", "0001_initial"),
    ]

    operations = [
        # =====================================================
        # ⚠️ IMPORTANT (DEV DB PATCH)
        #
        # exam_* 필드들은 과거 migration / 수동 SQL로
        # 이미 DB에 존재하는 상태이므로
        # AddField를 다시 수행하면 DuplicateColumn 오류 발생.
        #
        # 따라서:
        # - DB 스키마 변경은 수행하지 않음
        # - Django migration state만 최신 모델과 정렬
        #
        # 즉, "noop migration" 역할
        # =====================================================

        migrations.SeparateDatabaseAndState(
            database_operations=[
                # ❌ DB에는 아무 작업도 하지 않음
            ],
            state_operations=[
                # ✅ Django state에만 필드 존재를 명시

                migrations.AddField(
                    model_name="sessionprogress",
                    name="exam_attempted",
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name="sessionprogress",
                    name="exam_passed",
                    field=models.BooleanField(default=False),
                ),
                migrations.AddField(
                    model_name="sessionprogress",
                    name="exam_aggregate_score",
                    field=models.FloatField(null=True, blank=True),
                ),
                migrations.AddField(
                    model_name="sessionprogress",
                    name="exam_meta",
                    field=models.JSONField(null=True, blank=True),
                ),
            ],
        ),
    ]
