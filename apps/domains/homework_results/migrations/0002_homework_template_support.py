# Generated for homework template support (시험과 동일: 템플릿 저장·다른 강의 불러오기·통계 합산)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("homework_results", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="homework",
            name="homework_type",
            field=models.CharField(
                choices=[("template", "템플릿"), ("regular", "일반")],
                db_index=True,
                default="regular",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="homework",
            name="template_homework",
            field=models.ForeignKey(
                blank=True,
                help_text="일반 과제가 참조하는 템플릿",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="derived_homeworks",
                to="homework_results.homework",
            ),
        ),
        migrations.AlterField(
            model_name="homework",
            name="session",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                help_text="일반(regular) 과제는 필수. 템플릿은 null.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="homeworks",
                to="lectures.session",
            ),
        ),
    ]
