# PATH: apps/domains/homework_results/migrations/0003_fix_homeworkscore_table.py
"""
✅ FIX MIGRATION (DB 정합성 보정)

목표:
- homework_results_homeworkscore 테이블을 "정식 생성"해서 DB를 맞춘다.
- 과거에 잘못된 state(db_table=homework_homeworkscore)가 있었더라도,
  RenameIndex / AlterModelTable 같은 위험한 자동 migration이 나오지 않도록 막는다.

중요:
- 이미 DB에 homework_results_homeworkscore가 존재하면,
  이 migration은 "적용되지 않거나" 에러가 날 수 있다.
  그 경우 --fake 로 state만 맞추면 된다.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("homework_results", "0002_homework_and_more"),
        ("lectures", "0002_remove_session_exam"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # ✅ DB에 실제 테이블 생성
            database_operations=[
                migrations.CreateModel(
                    name="HomeworkScore",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),

                        ("enrollment_id", models.PositiveIntegerField(db_index=True)),

                        ("score", models.FloatField(blank=True, null=True)),
                        ("max_score", models.FloatField(blank=True, null=True)),

                        ("teacher_approved", models.BooleanField(default=False)),
                        ("passed", models.BooleanField(default=False)),
                        ("clinic_required", models.BooleanField(default=False)),

                        ("is_locked", models.BooleanField(default=False)),
                        (
                            "lock_reason",
                            models.CharField(
                                blank=True,
                                choices=[
                                    ("GRADING", "채점중"),
                                    ("PUBLISHED", "게시됨"),
                                    ("MANUAL", "수동잠금"),
                                    ("OTHER", "기타"),
                                ],
                                max_length=30,
                                null=True,
                            ),
                        ),

                        ("updated_by_user_id", models.PositiveIntegerField(blank=True, null=True)),
                        ("meta", models.JSONField(blank=True, null=True)),

                        (
                            "session",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="homework_scores",
                                to="lectures.session",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "homework_results_homeworkscore",
                        "ordering": ["-updated_at", "-id"],
                    },
                ),
                migrations.AddConstraint(
                    model_name="homeworkscore",
                    constraint=models.UniqueConstraint(
                        fields=("enrollment_id", "session"),
                        name="unique_homework_score_per_enrollment_session",
                    ),
                ),
                migrations.AddIndex(
                    model_name="homeworkscore",
                    index=models.Index(
                        fields=["enrollment_id", "updated_at"],
                        name="hwres_enroll_upd_idx",
                    ),
                ),
                migrations.AddIndex(
                    model_name="homeworkscore",
                    index=models.Index(
                        fields=["session", "updated_at"],
                        name="hwres_session_upd_idx",
                    ),
                ),
            ],

            # ✅ state는 "이미 모델이 있다"고만 맞춰준다.
            # (모델 state를 새로 만들지 않아서, RenameIndex/AlterTable 유발 방지)
            state_operations=[],
        ),
    ]
