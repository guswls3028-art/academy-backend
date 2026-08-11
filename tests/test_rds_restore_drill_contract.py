from pathlib import Path

import pytest
from django.apps import apps


REPO_ROOT = Path(__file__).resolve().parents[1]
DRILL_SCRIPT = REPO_ROOT / "scripts" / "v1" / "run-rds-restore-drill.ps1"


@pytest.mark.parametrize(
    ("app_label", "model_name"),
    [
        ("core", "Tenant"),
        ("core", "User"),
        ("students", "Student"),
        ("exams", "Exam"),
        ("results", "ExamResult"),
        ("fees", "FeePayment"),
        ("messaging", "ScheduledNotification"),
    ],
)
def test_rds_restore_drill_uses_registered_critical_table_names(app_label, model_name):
    model = apps.get_model(app_label, model_name)
    script = DRILL_SCRIPT.read_text(encoding="utf-8")

    assert f'"{model._meta.db_table}"' in script


def test_rds_restore_drill_includes_django_migration_ledger():
    script = DRILL_SCRIPT.read_text(encoding="utf-8")

    assert '"django_migrations"' in script
