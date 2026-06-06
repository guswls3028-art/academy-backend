from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0028_remove_clinic_check_out_choice"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificationlog",
            name="source_tenant_id",
            field=models.IntegerField(
                blank=True,
                db_index=True,
                help_text="오너 대리발송의 원 소속 테넌트 ID",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="notificationlog",
            name="target_type",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="student, parent, account 등",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="notificationlog",
            name="target_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="대상 식별자",
                max_length=80,
            ),
        ),
        migrations.AddField(
            model_name="notificationlog",
            name="target_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="대상 표시명",
                max_length=80,
            ),
        ),
    ]
