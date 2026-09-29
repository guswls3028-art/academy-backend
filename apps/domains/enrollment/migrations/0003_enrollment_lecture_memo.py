from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("enrollment", "0002_student_deletion_status_snapshot")]

    operations = [
        migrations.AddField(
            model_name="enrollment",
            name="lecture_memo",
            field=models.TextField(blank=True, default="", db_default=""),
        ),
    ]
