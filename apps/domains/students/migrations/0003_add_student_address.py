# Generated manually: Student + StudentRegistrationRequest address (선택)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0002_student_registration_request"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="address",
            field=models.CharField(blank=True, help_text="주소 (선택)", max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="studentregistrationrequest",
            name="address",
            field=models.CharField(blank=True, help_text="주소 (선택)", max_length=255, null=True),
        ),
    ]
