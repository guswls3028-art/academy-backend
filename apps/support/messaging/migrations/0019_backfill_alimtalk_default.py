"""
기존 AutoSendConfig의 message_mode를 sms → alimtalk로 일괄 전환.
전 테넌트 알림톡 기본값 적용.
"""
from django.db import migrations


def forward(apps, schema_editor):
    AutoSendConfig = apps.get_model("messaging", "AutoSendConfig")
    updated = AutoSendConfig.objects.filter(message_mode="sms").update(message_mode="alimtalk")
    if updated:
        print(f"\n  → {updated}건 AutoSendConfig message_mode: sms → alimtalk 전환 완료")


def reverse(apps, schema_editor):
    # 되돌리기: alimtalk → sms (원복은 수동 판단)
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0018_alimtalk_default_and_video_trigger"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
