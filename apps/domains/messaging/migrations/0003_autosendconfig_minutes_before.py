# AutoSendConfig: 타이머 설정 (N분 전 발송) — 사용자 커스텀용 환경 제공
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0002_autosendconfig"),
    ]

    operations = [
        migrations.AddField(
            model_name="autosendconfig",
            name="minutes_before",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
