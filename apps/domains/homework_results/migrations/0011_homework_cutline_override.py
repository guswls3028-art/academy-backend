from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("homework_results", "0010_homeworkscore_updated_by_fk"),
    ]

    operations = [
        migrations.AddField(
            model_name="homework",
            name="cutline_mode",
            field=models.CharField(
                blank=True,
                choices=[("PERCENT", "퍼센트 (%)"), ("COUNT", "점수")],
                help_text="비어 있으면 차시 공통 과제 정책을 사용한다.",
                max_length=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="homework",
            name="cutline_value",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="PERCENT: 0~100, COUNT: 이 과제의 원점수 커트라인.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="homework",
            name="round_unit_percent",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="과제별 퍼센트 반올림 단위. 비어 있으면 차시 정책을 사용한다.",
                null=True,
            ),
        ),
    ]
