# Generated migration for access_mode field

from django.db import migrations, models


def migrate_rule_to_access_mode(apps, schema_editor):
    """
    Migrate existing rule values to access_mode:
    - free → FREE_REVIEW
    - once → PROCTORED_CLASS
    - blocked → BLOCKED
    """
    VideoPermission = apps.get_model('video', 'VideoPermission')
    
    # Map legacy rule to access_mode
    rule_mapping = {
        'free': 'FREE_REVIEW',
        'once': 'PROCTORED_CLASS',
        'blocked': 'BLOCKED',
    }
    
    for perm in VideoPermission.objects.all():
        rule_value = perm.rule or 'free'
        access_mode_value = rule_mapping.get(rule_value, 'FREE_REVIEW')
        perm.access_mode = access_mode_value
        perm.save(update_fields=['access_mode'])


class Migration(migrations.Migration):

    dependencies = [
        ('video', '0002_add_playback_session_fields'),
    ]

    operations = [
        # Add access_mode field
        migrations.AddField(
            model_name='videopermission',
            name='access_mode',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('FREE_REVIEW', '복습'),
                    ('PROCTORED_CLASS', '온라인 수업 대체'),
                    ('BLOCKED', '제한'),
                ],
                default='FREE_REVIEW',
                db_index=True,
                help_text='Access mode: FREE_REVIEW, PROCTORED_CLASS, or BLOCKED',
            ),
        ),
        
        # Migrate existing rule values to access_mode
        migrations.RunPython(
            code=migrate_rule_to_access_mode,
            reverse_code=migrations.RunPython.noop,
        ),
        
        # Make rule field nullable for backward compatibility
        migrations.AlterField(
            model_name='videopermission',
            name='rule',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('free', '무제한'),
                    ('once', '1회 제한'),
                    ('blocked', '제한'),
                ],
                default='free',
                null=True,
                blank=True,
                help_text='DEPRECATED: Use access_mode instead',
            ),
        ),
    ]
