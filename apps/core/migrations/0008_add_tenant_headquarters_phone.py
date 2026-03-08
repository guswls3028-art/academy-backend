# Tenant 본부 전화번호 — 학생앱 "본부 진입게이트" 노출용
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_add_student_registration_auto_approve"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="headquarters_phone",
            field=models.CharField(blank=True, max_length=50),
        ),
    ]
