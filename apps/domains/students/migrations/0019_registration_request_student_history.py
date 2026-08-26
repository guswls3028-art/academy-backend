import django.db.models.deletion
from django.db import migrations, models


ACADEMY_MIGRATION_PHASE = "contract"
ACADEMY_MIGRATION_REASON = (
    "Drop only the registration-to-student uniqueness constraint so rolling old and new "
    "runtimes preserve existing links while explicit recovery adds another audit link."
)


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
