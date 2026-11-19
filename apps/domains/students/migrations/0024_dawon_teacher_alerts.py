from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("students", "0023_class_group_reference")]
    operations = [migrations.CreateModel(
        name="DawonTeacherAlertSettings",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("tenant", models.ForeignKey(on_delete=models.CASCADE, to="core.tenant")),
            ("teacher", models.ForeignKey(on_delete=models.CASCADE, to=settings.AUTH_USER_MODEL)),
            ("rules", models.JSONField(default=dict)),
        ],
        options={"constraints": [models.UniqueConstraint(fields=("tenant", "teacher"), name="students_dawon_teacher_alerts")]},
    )]
