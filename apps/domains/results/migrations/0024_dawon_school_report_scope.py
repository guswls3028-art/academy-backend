from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("results", "0023_school_report_collection")]
    operations = [
        migrations.AddField(model_name="studentreportedscore", name="school_at_submission",
                            field=models.CharField(max_length=100, null=True, blank=True)),
        migrations.AddField(model_name="studentreportedscore", name="year_grade_at_submission",
                            field=models.PositiveSmallIntegerField(null=True, blank=True)),
    ]
