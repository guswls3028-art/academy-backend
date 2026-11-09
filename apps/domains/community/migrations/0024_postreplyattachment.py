import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("community", "0023_protect_post_mapping_scope")]

    operations = [
        migrations.CreateModel(
            name="PostReplyAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("r2_key", models.CharField(max_length=512, unique=True)),
                ("original_name", models.CharField(max_length=255)),
                ("size_bytes", models.BigIntegerField()),
                ("content_type", models.CharField(max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reply", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="attachments",
                    to="community.postreply",
                )),
            ],
            options={"ordering": ["id"]},
        ),
    ]
