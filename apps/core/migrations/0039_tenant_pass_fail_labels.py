"""
Tenant.pass_label / Tenant.fail_label 추가.
학원장이 합격/불합격 표기를 자유 텍스트로 커스텀 가능.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0038_video_session_limits"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="pass_label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="합격 표기 라벨 커스텀. 빈값=기본값 '합격'.",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="fail_label",
            field=models.CharField(
                blank=True,
                default="",
                help_text="불합격 표기 라벨 커스텀. 빈값=기본값 '불합격'.",
                max_length=20,
            ),
        ),
    ]
