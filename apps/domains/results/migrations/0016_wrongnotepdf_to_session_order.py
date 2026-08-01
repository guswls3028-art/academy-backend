from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("results", "0015_resultitem_include_in_wrong_note"),
    ]

    operations = [
        migrations.AddField(
            model_name="wrongnotepdf",
            name="to_session_order",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
