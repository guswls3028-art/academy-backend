from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("staffs", "0007_unique_open_work_record"),
    ]

    operations = [
        migrations.AddField(
            model_name="payrollsnapshot",
            name="staff_name",
            field=models.CharField(
                blank=True,
                help_text="월마감 당시 직원명",
                max_length=100,
            ),
        ),
    ]
