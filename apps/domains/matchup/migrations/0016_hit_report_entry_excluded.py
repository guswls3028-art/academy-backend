# Generated for HitReportEntry.excluded — 강사가 매칭 못한 Q를 PDF에서 빼는 토글 (2026-05-05).
# Zero-downtime: BooleanField default=False는 PG에서 metadata-only 변경 (즉시).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matchup", "0015_vector_sync_trigger"),
    ]

    operations = [
        migrations.AddField(
            model_name="matchuphitreportentry",
            name="excluded",
            field=models.BooleanField(default=False),
        ),
    ]
