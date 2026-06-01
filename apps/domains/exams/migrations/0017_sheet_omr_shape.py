from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0016_alter_exam_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="sheet",
            name="choice_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="sheet",
            name="essay_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
