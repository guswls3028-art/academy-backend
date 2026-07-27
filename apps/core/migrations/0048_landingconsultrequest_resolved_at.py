from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0047_landing_consult_privacy_consent"),
    ]

    operations = [
        migrations.AddField(
            model_name="landingconsultrequest",
            name="resolved_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
