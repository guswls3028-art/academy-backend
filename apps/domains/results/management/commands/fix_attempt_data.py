"""
Pre-migration data fix: submission_id=0 -> NULL for ExamAttempt.
These are attempts created without a real submission (e.g., clinic direct entry).
Usage: python manage.py fix_attempt_data [--dry-run]
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Fix ExamAttempt records with submission_id=0 -> NULL"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would be changed without modifying data")

    def handle(self, *args, **options):
        from apps.domains.results.models import ExamAttempt

        dry_run = options.get("dry_run", False)

        bad_records = ExamAttempt.objects.filter(submission_id=0)
        count = bad_records.count()

        self.stdout.write(f"ExamAttempt with submission_id=0: {count} records")

        if count == 0:
            self.stdout.write("Nothing to fix.")
            return

        # Show details
        for a in bad_records[:20]:
            self.stdout.write(
                f"  id={a.id} exam={a.exam_id} enrollment={a.enrollment_id} "
                f"attempt_index={a.attempt_index} is_representative={a.is_representative} "
                f"status={a.status}"
            )

        if dry_run:
            self.stdout.write(f"\n[DRY RUN] Would update {count} records: submission_id=0 -> NULL")
            return

        with transaction.atomic():
            updated = bad_records.update(submission_id=None)
            self.stdout.write(f"\nFIXED: {updated} records updated (submission_id=0 -> NULL)")
