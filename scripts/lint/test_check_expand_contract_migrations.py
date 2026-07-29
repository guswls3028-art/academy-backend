from __future__ import annotations

import unittest

from scripts.lint.check_expand_contract_migrations import (
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
