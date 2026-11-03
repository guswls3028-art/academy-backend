from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0063_account_notice_credentials")]

    operations = [
        migrations.AddField(
            model_name="tenant", name="review_limit_new_uploads",
            field=models.BooleanField(default=False, db_default=False),
        ),
    ]
