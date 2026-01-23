# PATH: apps/domains/homework_results/migrations/0004_alter_homeworkscore_table.py
"""
✅ STATE ONLY FIX

목표:
- DB는 건드리지 않는다 (이미 homework_results_homeworkscore 테이블 존재)
- Django migration "state"만 올바른 table명으로 맞춘다.
- 이렇게 해야 이후 makemigrations에서 RenameTable이 다시 생성되지 않는다.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("homework_results", "0003_fix_homeworkscore_table"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],  # ✅ DB 작업 절대 없음
            state_operations=[
                migrations.AlterModelTable(
                    name="homeworkscore",
                    table="homework_results_homeworkscore",
                ),
            ],
        ),
    ]
