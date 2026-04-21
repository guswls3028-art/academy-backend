# Generated for MatchupDocument.meta field (segmentation_method 등 관측용)
#
# 특이사항: 프로덕션 DB에 meta 컬럼이 Django 마이그레이션을 거치지 않고 수동으로
# 추가된 드리프트 상태. SeparateDatabaseAndState + IF NOT EXISTS 패턴으로
# Django state는 동기화하되 DB 변경은 idempotent하게 처리.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matchup", "0003_alter_matchupproblem_unique_together_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=[
                        "ALTER TABLE matchup_matchupdocument "
                        "ADD COLUMN IF NOT EXISTS meta jsonb NOT NULL DEFAULT '{}'::jsonb",
                        "ALTER TABLE matchup_matchupdocument "
                        "ALTER COLUMN meta SET DEFAULT '{}'::jsonb",
                    ],
                    reverse_sql=[
                        "ALTER TABLE matchup_matchupdocument DROP COLUMN IF EXISTS meta",
                    ],
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="matchupdocument",
                    name="meta",
                    field=models.JSONField(blank=True, default=dict),
                ),
            ],
        ),
    ]
