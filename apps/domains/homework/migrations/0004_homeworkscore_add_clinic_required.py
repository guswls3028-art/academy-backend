# PATH: apps/domains/homework/migrations/0004_homeworkscore_add_clinic_required.py
"""
✅ MVP PATCH
- HomeworkScore에 clinic_required 필드 추가
- Scores 탭에서 클리닉 원인 표시를 위해 필요
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("homework", "0003_homeworkpolicy"),
    ]

    operations = [
        migrations.AddField(
            model_name="homeworkscore",
            name="clinic_required",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="homeworkpolicy",
            name="round_unit_percent",
            field=models.PositiveSmallIntegerField(default=5),
        ),
    ]
