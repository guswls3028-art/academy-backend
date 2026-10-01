from django.db import migrations, models

ACADEMY_MIGRATION_PHASE = "expand"
ACADEMY_MIGRATION_REASON = "승인 안내에 실제 가입 비밀번호를 사용하기 위한 암호문을 추가한다. 기존 신청 hash와 데이터는 보존한다."


class Migration(migrations.Migration):
    dependencies = [("students", "0019_registration_request_student_history")]
    operations = [migrations.AddField(model_name="studentregistrationrequest", name="initial_password_ciphertext", field=models.TextField(blank=True, default="", editable=False))]
