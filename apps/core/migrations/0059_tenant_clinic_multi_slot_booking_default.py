from django.db import migrations, models


def set_initial_tenant_defaults(apps, schema_editor):
    Tenant = apps.get_model("core", "Tenant")
    Tenant.objects.filter(code__in=["tchul", "godmin"]).update(
        clinic_allow_multi_slot_booking_default=False
    )
    Tenant.objects.filter(code="limglish").update(
        clinic_allow_multi_slot_booking_default=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0058_tenant_controls_messaging_activation"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="clinic_allow_multi_slot_booking_default",
            field=models.BooleanField(
                db_default=False,
                default=False,
                help_text="새 클리닉 세션의 같은 날 여러 시간대 예약 허용 기본값입니다.",
            ),
        ),
        migrations.RunPython(
            set_initial_tenant_defaults,
            migrations.RunPython.noop,
        ),
    ]
