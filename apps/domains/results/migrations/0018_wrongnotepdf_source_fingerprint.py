from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("results", "0017_wrongnotepdf_output_format"),
    ]

    operations = [
        migrations.AddField(
            model_name="wrongnotepdf",
            name="source_fingerprint",
            field=models.CharField(
                blank=True,
                db_default="",
                help_text="생성 요청 시점의 오답·문항·해설 내용 SHA-256",
                max_length=64,
            ),
        ),
    ]
