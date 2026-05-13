from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0012_student_schedule_hidden_before"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="schedule_hidden_ids",
            field=models.JSONField(
                default=list,
                blank=True,
                help_text="학생앱 일정 개별 숨김 ID 목록. 양수=LectureSession.id, 음수=ClinicSessionParticipant.id*-1. me/ 응답 ID 규약과 동일. 실제 데이터는 그대로 유지.",
            ),
        ),
    ]
