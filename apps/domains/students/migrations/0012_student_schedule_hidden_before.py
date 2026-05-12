from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0011_school_level_mode_elementary"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="schedule_hidden_before",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="학생앱 일정 휴지통: 이 날짜를 포함하여 그 이전 차시/클리닉 예약은 학생 화면에서 숨김. 실제 데이터는 그대로 유지.",
            ),
        ),
    ]
