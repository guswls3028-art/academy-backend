from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("landing_public", "0009_publicresourcepost_publicresourcefile_and_more")]
    operations = [migrations.AlterField(
        model_name="publicresourcefile", name="extension",
        field=models.CharField(blank=True, max_length=200),
    )]
