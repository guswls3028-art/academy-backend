from django.db import migrations, models


def set_initial_session_policies(apps, schema_editor):
    Session = apps.get_model("clinic", "Session")
    Session.objects.exclude(tenant__code="limglish").update(
        allow_multi_slot_booking=False
    )
    Session.objects.filter(tenant__code="limglish").update(
        allow_multi_slot_booking=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("clinic", "0016_session_time_preference_and_staff_memo"),
        ("core", "0059_tenant_clinic_multi_slot_booking_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="session",
            name="allow_multi_slot_booking",
            field=models.BooleanField(
                db_default=False,
                default=False,
                help_text="같은 날짜의 다른 클리닉 시간대도 함께 예약할 수 있으면 True.",
            ),
        ),
        migrations.RunPython(
            set_initial_session_policies,
            migrations.RunPython.noop,
        ),
    ]
