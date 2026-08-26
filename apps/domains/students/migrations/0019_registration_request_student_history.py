import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("students", "0018_student_support_session"),
    ]

    operations = [
        migrations.AlterField(
            model_name="studentregistrationrequest",
            name="student",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="registration_requests",
                to="students.student",
            ),
        ),
    ]
