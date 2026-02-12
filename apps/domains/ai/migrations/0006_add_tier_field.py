# Generated migration: Add tier field to AIJobModel

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ai_domain', '0005_alter_aijobmodel_error_message_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='aijobmodel',
            name='tier',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('lite', 'Lite'),
                    ('basic', 'Basic'),
                    ('premium', 'Premium'),
                ],
                default='basic',
                db_index=True,
                help_text='Tier determines queue routing and processing capabilities',
            ),
        ),
        migrations.AddIndex(
            model_name='aijobmodel',
            index=models.Index(fields=['tier', 'status', 'next_run_at'], name='ai_job_tier_stat_next_idx'),
        ),
    ]
