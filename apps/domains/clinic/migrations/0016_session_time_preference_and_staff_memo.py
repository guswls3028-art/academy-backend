from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clinic", "0015_sessionparticipantplanitem"),
    ]

    operations = [
        migrations.AddField(
            model_name="session",
            name="allow_time_preference",
            field=models.BooleanField(
                db_default=False,
                default=False,
                help_text="학생이 세션 범위 안의 희망 시작·종료 시각을 요청할 수 있으면 True.",
            ),
        ),
        migrations.AddField(
            model_name="sessionparticipant",
            name="preferred_start_time",
            field=models.TimeField(
                blank=True,
                help_text="세션 안에서 요청한 희망 시작 시각. 실제 예약 시작 시각과 별개.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="sessionparticipant",
            name="preferred_end_time",
            field=models.TimeField(
                blank=True,
                help_text="세션 안에서 요청한 희망 종료 시각. 실제 예약 종료 시각과 별개.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="sessionparticipant",
            name="staff_memo",
            field=models.TextField(
                blank=True,
                db_default="",
                default="",
                help_text="학생·학부모에게 노출하지 않는 교직원 인수인계 메모",
            ),
        ),
    ]
