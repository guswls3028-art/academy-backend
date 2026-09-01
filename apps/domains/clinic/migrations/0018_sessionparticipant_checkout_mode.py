from django.db import migrations, models


def backfill_checkout_mode(apps, schema_editor):
    SessionParticipant = apps.get_model("clinic", "SessionParticipant")
    SessionParticipant.objects.filter(
        checked_out_at__isnull=False,
        checked_in_at__isnull=False,
        checkout_mode="",
    ).update(checkout_mode="arrival_recorded")


class Migration(migrations.Migration):
    dependencies = [
        ("clinic", "0017_session_allow_multi_slot_booking"),
    ]

    operations = [
        migrations.AddField(
            model_name="sessionparticipant",
            name="checkout_mode",
            field=models.CharField(
                blank=True,
                choices=[
                    ("arrival_recorded", "Arrival recorded"),
                    ("arrival_not_recorded", "Arrival not recorded"),
                ],
                db_default="",
                default="",
                help_text=(
                    "하원 처리 당시 등원 기록의 존재 여부. arrival_not_recorded는 등원을 "
                    "추정하거나 생성하지 않은 명시적 예외 처리다."
                ),
                max_length=24,
            ),
        ),
        migrations.RunPython(backfill_checkout_mode, migrations.RunPython.noop),
    ]
