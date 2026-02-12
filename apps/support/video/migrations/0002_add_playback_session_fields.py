# Generated migration: Add fields to VideoPlaybackSession for DB-based session management

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('video', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='videoplaybacksession',
            name='expires_at',
            field=models.DateTimeField(null=True, blank=True, db_index=True, help_text='Session expiration time'),
        ),
        migrations.AddField(
            model_name='videoplaybacksession',
            name='last_seen',
            field=models.DateTimeField(null=True, blank=True, help_text='Last heartbeat time'),
        ),
        migrations.AddField(
            model_name='videoplaybacksession',
            name='violated_count',
            field=models.IntegerField(default=0, help_text='Number of violations'),
        ),
        migrations.AddField(
            model_name='videoplaybacksession',
            name='total_count',
            field=models.IntegerField(default=0, help_text='Total event count'),
        ),
        migrations.AddField(
            model_name='videoplaybacksession',
            name='is_revoked',
            field=models.BooleanField(default=False, db_index=True, help_text='Whether session is revoked'),
        ),
        migrations.AddIndex(
            model_name='videoplaybacksession',
            index=models.Index(fields=['status', 'expires_at'], name='video_playback_status_expires_idx'),
        ),
        migrations.AddIndex(
            model_name='videoplaybacksession',
            index=models.Index(fields=['enrollment', 'status'], name='video_playback_enrollment_status_idx'),
        ),
    ]
