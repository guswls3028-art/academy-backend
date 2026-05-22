from django.db import migrations, models
from django.db.models import Q


SENSITIVE_NOTIFICATION_TYPES = [
    "registration_approved_student",
    "registration_approved_parent",
    "password_find_otp",
    "password_reset_student",
    "password_reset_parent",
]

PLACEHOLDER = "[보안] 계정/인증 알림 본문은 저장하지 않습니다."


def redact_sensitive_message_bodies(apps, schema_editor):
    NotificationLog = apps.get_model("messaging", "NotificationLog")
    sensitive_query = (
        Q(notification_type__in=SENSITIVE_NOTIFICATION_TYPES)
        | Q(message_body__icontains="비밀번호")
        | Q(message_body__icontains="임시비밀번호")
        | Q(message_body__icontains="인증번호")
        | Q(message_body__icontains="password")
    )
    NotificationLog.objects.filter(sensitive_query).exclude(message_body="").update(
        message_body=PLACEHOLDER,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0026_cleanup_dead_triggers"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notificationlog",
            name="message_body",
            field=models.TextField(
                blank=True,
                default="",
                help_text="보안 마스킹이 적용된 발송 메시지 본문",
            ),
        ),
        migrations.RunPython(redact_sensitive_message_bodies, migrations.RunPython.noop),
    ]
