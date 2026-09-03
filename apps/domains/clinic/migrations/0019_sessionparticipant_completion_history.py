from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clinic", "0018_sessionparticipant_checkout_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="sessionparticipant",
            name="completion_history",
            field=models.JSONField(
                blank=True,
                db_default=models.Value([], output_field=models.JSONField()),
                default=list,
                help_text="완료/완료 취소의 append-only 감사 이력",
            ),
        ),
    ]
