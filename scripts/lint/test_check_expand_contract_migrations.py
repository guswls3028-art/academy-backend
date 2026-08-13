from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from scripts.lint.check_expand_contract_migrations import (
    _is_safe_charfield_widening,
    _safe_charfield_widenings,
    inspect_modified_migration,
    inspect_new_migration,
)


SAFE_NULLABLE_ADD = """
from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.AddField(
            model_name="student",
            name="nickname",
            field=models.CharField(max_length=30, null=True),
        ),
    ]
"""

UNSAFE_REMOVE = """
from django.db import migrations
class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.RemoveField(model_name="student", name="legacy_name"),
    ]
"""

REVIEWED_CONTRACT = """
from django.db import migrations
ACADEMY_MIGRATION_PHASE = "contract"
ACADEMY_MIGRATION_REASON = "The expand release stopped all reads of legacy_name."
class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.RemoveField(model_name="student", name="legacy_name"),
    ]
"""

BLOCKING_INDEX = """
from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.AddIndex(
            model_name="student",
            index=models.Index(fields=["name"], name="student_name_idx"),
        ),
    ]
"""

STATE_ONLY_ALTER = """
from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="exam",
                    name="status",
                    field=models.CharField(max_length=20),
                ),
            ],
        ),
    ]
"""

SEPARATE_WITH_DATABASE_OPERATION = """
from django.db import migrations
class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL("DROP TABLE legacy_table"),
            ],
            state_operations=[],
        ),
    ]
"""

SAFE_CHARFIELD_WIDEN = """
from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [("demo", "0001_initial")]
    operations = [
        migrations.AlterField(
            model_name="example",
            name="code",
            field=models.CharField(blank=True, max_length=50),
        ),
    ]
"""

SAFE_CHARFIELD_INITIAL = """
from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="Example",
            fields=[
                ("code", models.CharField(blank=True, max_length=20)),
            ],
        ),
    ]
"""


class ExpandContractMigrationGuardTests(unittest.TestCase):
    def test_nullable_add_is_expand_safe(self) -> None:
        self.assertEqual(
            inspect_new_migration(
                "apps/demo/migrations/0002.py",
                SAFE_NULLABLE_ADD,
                allow_contract=False,
            ),
            [],
        )

    def test_destructive_operation_needs_contract_metadata(self) -> None:
        findings = inspect_new_migration(
            "apps/demo/migrations/0002.py",
            UNSAFE_REMOVE,
            allow_contract=False,
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("require ACADEMY_MIGRATION_PHASE", findings[0].message)

    def test_non_concurrent_index_is_not_accepted_as_expand_safe(self) -> None:
        findings = inspect_new_migration(
            "apps/demo/migrations/0002.py",
            BLOCKING_INDEX,
            allow_contract=False,
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("AddIndex", findings[0].message)

    def test_state_only_alter_is_expand_safe(self) -> None:
        self.assertEqual(
            inspect_new_migration(
                "apps/demo/migrations/0002.py",
                STATE_ONLY_ALTER,
                allow_contract=False,
            ),
            [],
        )

    def test_separate_database_operation_still_needs_contract_review(self) -> None:
        findings = inspect_new_migration(
            "apps/demo/migrations/0002.py",
            SEPARATE_WITH_DATABASE_OPERATION,
            allow_contract=False,
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("SeparateDatabaseAndState", findings[0].message)

    def test_only_max_length_increase_is_safe_charfield_widening(self) -> None:
        before = ast.parse(
            "models.CharField(blank=True, max_length=20)", mode="eval"
        ).body
        after = ast.parse(
            "models.CharField(blank=True, max_length=50)", mode="eval"
        ).body
        changed_option = ast.parse(
            "models.CharField(blank=False, max_length=50)", mode="eval"
        ).body

        self.assertTrue(_is_safe_charfield_widening(before, after))
        self.assertFalse(_is_safe_charfield_widening(before, changed_option))
        self.assertFalse(_is_safe_charfield_widening(after, before))

    def test_run_verifies_widening_against_same_app_migration_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = Path(temporary_directory)
            migration_dir = repo / "apps" / "demo" / "migrations"
            migration_dir.mkdir(parents=True)
            (migration_dir / "0001_initial.py").write_text(
                SAFE_CHARFIELD_INITIAL,
                encoding="utf-8",
            )
            path = "apps/demo/migrations/0002_widen_code.py"
            (repo / path).write_text(SAFE_CHARFIELD_WIDEN, encoding="utf-8")
            tree = ast.parse(SAFE_CHARFIELD_WIDEN, filename=path)

            safe_fields = _safe_charfield_widenings(repo, path, tree)

        self.assertEqual(safe_fields, {("example", "code")})
        self.assertEqual(
            inspect_new_migration(
                path,
                SAFE_CHARFIELD_WIDEN,
                allow_contract=False,
                safe_alter_fields=safe_fields,
            ),
            [],
        )

    def test_contract_is_blocked_without_explicit_dispatch(self) -> None:
        findings = inspect_new_migration(
            "apps/demo/migrations/0002.py",
            REVIEWED_CONTRACT,
            allow_contract=False,
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("automatic push deploys", findings[0].message)

    def test_explicit_reviewed_contract_is_allowed(self) -> None:
        self.assertEqual(
            inspect_new_migration(
                "apps/demo/migrations/0002.py",
                REVIEWED_CONTRACT,
                allow_contract=True,
            ),
            [],
        )

    def test_comment_only_change_preserves_migration_semantics(self) -> None:
        after = SAFE_NULLABLE_ADD.replace(
            "class Migration",
            "# clarified historical invariant\nclass Migration",
        )
        self.assertEqual(
            inspect_modified_migration(
                "apps/demo/migrations/0002.py",
                SAFE_NULLABLE_ADD,
                after,
            ),
            [],
        )

    def test_operation_change_in_existing_migration_is_blocked(self) -> None:
        after = SAFE_NULLABLE_ADD.replace("null=True", "null=False")
        findings = inspect_modified_migration(
            "apps/demo/migrations/0002.py",
            SAFE_NULLABLE_ADD,
            after,
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("existing migration", findings[0].message)


if __name__ == "__main__":
    unittest.main()
