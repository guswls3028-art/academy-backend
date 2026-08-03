from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("results", "0016_wrongnotepdf_to_session_order")]

    operations = [
        migrations.AddField(
            model_name="wrongnotepdf",
            name="output_format",
            field=models.CharField(
                choices=[("pdf", "PDF"), ("hwpx", "한글(HWPX)")],
                default="pdf",
                max_length=8,
            ),
        ),
    ]
