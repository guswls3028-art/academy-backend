from django.db import migrations, models

ACADEMY_MIGRATION_PHASE = "expand"
ACADEMY_MIGRATION_REASON = "암호화된 계정 안내와 학원별 초기 비밀번호 설정을 추가하며 기존 비밀번호는 보존한다."


class Migration(migrations.Migration):
    dependencies = [("core", "0062_alter_tenant_clinic_use_daily_random_default")]
    operations = [
        migrations.AddField(model_name="tenant", name="account_password_policy", field=models.JSONField(blank=True, default=dict, db_default={}, editable=False)),
        migrations.AddField(model_name="user", name="account_notice_password_ciphertext", field=models.TextField(blank=True, default="", db_default="", editable=False)),
    ]
