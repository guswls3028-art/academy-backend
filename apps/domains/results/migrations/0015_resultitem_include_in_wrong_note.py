from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("results", "0014_studentreportedscore_exam_name_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="resultitem",
            name="include_in_wrong_note",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "정오 여부와 무관하게 복습/오답노트에 포함할지 여부. "
                    "예: Ymath 엑셀의 숫자 0은 정답이면서 이 값이 true다."
                ),
            ),
        ),
    ]
