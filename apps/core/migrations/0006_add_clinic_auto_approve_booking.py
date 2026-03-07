# Generated manually for clinic_auto_approve_booking

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0005_add_clinic_use_daily_random"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="clinic_auto_approve_booking",
            field=models.BooleanField(
                default=False,
                help_text="True면 학생 예약 신청을 자동 승인(booked)합니다.",
            ),
        ),
    ]
