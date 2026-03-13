"""
ScoreEditDraft: unique constraint에 tenant_id 추가하여 cross-tenant 충돌 방지.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("results", "0003_score_edit_draft"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="scoreeditdraft",
            unique_together={("tenant_id", "session_id", "editor_user_id")},
        ),
    ]
