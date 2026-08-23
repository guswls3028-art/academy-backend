import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clinic', '0014_sessionparticipant_checked_out_at_and_more'),
        ('progress', '0015_assessmentcorrection_source_fingerprint'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SessionParticipantPlanItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('removed_at', models.DateTimeField(blank=True, null=True)),
                ('removal_reason', models.CharField(blank=True, default='', max_length=80)),
                ('clinic_link', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='session_participant_plan_items', to='progress.cliniclink')),
                ('participant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='plan_items', to='clinic.sessionparticipant')),
                ('removed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='removed_clinic_participant_plan_items', to=settings.AUTH_USER_MODEL)),
                ('selected_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='selected_clinic_participant_plan_items', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['clinic_link_id', 'id'],
                'indexes': [models.Index(fields=['participant', 'removed_at'], name='clinic_sess_partici_713d61_idx'), models.Index(fields=['clinic_link', 'removed_at'], name='clinic_sess_clinic__1ed1ed_idx')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('removed_at__isnull', True)), fields=('participant', 'clinic_link'), name='uniq_active_clinic_participant_plan_item')],
            },
        ),
    ]
