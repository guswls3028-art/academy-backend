# Generated manually — 강의딱지 2글자 (강의 생성 모달에서 설정)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lectures", "0006_alter_lecture_lecture_time_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="lecture",
            name="chip_label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="강의딱지 2글자 (미입력 시 제목 앞 2자 사용)",
                max_length=2,
            ),
        ),
    ]
