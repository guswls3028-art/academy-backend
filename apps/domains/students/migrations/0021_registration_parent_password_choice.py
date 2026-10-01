from django.db import migrations, models

ACADEMY_MIGRATION_PHASE = "expand"
ACADEMY_MIGRATION_REASON = "가입 신청에서 선택한 학부모 비밀번호 방식과 암호문을 승인까지 보존한다. 기존 신청·계정은 변경하지 않는다."


class Migration(migrations.Migration):
    dependencies = [("students", "0020_registration_password_ciphertext")]
    operations = [
        migrations.AddField(model_name="studentregistrationrequest", name="parent_initial_password_mode", field=models.CharField(max_length=16, blank=True, default="", db_default="", editable=False)),
        migrations.AddField(model_name="studentregistrationrequest", name="parent_initial_password_ciphertext", field=models.TextField(blank=True, default="", db_default="", editable=False)),
    ]
