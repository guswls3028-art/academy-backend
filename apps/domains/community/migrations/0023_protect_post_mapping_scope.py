from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("community", "0022_support_request_idempotency")]

    operations = [
        migrations.AlterField(
            model_name="postmapping",
            name="node",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="post_mappings",
                to="community.scopenode",
            ),
        ),
    ]
