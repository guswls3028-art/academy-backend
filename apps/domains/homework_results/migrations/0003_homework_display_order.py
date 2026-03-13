from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("homework_results", "0002_homework_template_support"),
    ]

    operations = [
        migrations.AddField(
            model_name="homework",
            name="display_order",
            field=models.PositiveIntegerField(
                default=0,
                help_text="성적탭 내 표시 순서 (작을수록 앞)",
            ),
        ),
    ]
