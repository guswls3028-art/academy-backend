from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.domains.results.models import WrongNotePDF
from apps.domains.results.services.wrong_note_pdf_service import (
    delete_wrong_note_pdf_object,
)


class Command(BaseCommand):
    help = "Delete tracked R2 objects left by terminal failed wrong-note PDF jobs."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--older-than-minutes", type=int, default=5)
        parser.add_argument("--silent", action="store_true")

    def handle(self, *args, **options):
        limit = int(options["limit"])
        older_than_minutes = int(options["older_than_minutes"])
        if limit < 1 or limit > 500:
            raise CommandError("--limit must be between 1 and 500")
        if older_than_minutes < 0:
            raise CommandError("--older-than-minutes must be non-negative")

        cutoff = timezone.now() - timedelta(minutes=older_than_minutes)
        candidates = list(
            WrongNotePDF.objects.filter(
                status=WrongNotePDF.Status.FAILED,
                updated_at__lte=cutoff,
            )
            .exclude(file_path="")
            .only("id", "file_path")
            .order_by("id")[:limit]
        )

        cleaned = 0
        retained = 0
        for job in candidates:
            tracked_key = str(job.file_path or "")
            if not delete_wrong_note_pdf_object(tracked_key):
                retained += 1
                continue
            cleaned += WrongNotePDF.objects.filter(
                id=job.id,
                status=WrongNotePDF.Status.FAILED,
                file_path=tracked_key,
            ).update(
                file_path="",
                updated_at=timezone.now(),
            )

        if not options["silent"]:
            self.stdout.write(
                self.style.SUCCESS(
                    "wrong-note PDF cleanup complete: "
                    f"selected={len(candidates)} cleaned={cleaned} retained={retained}"
                )
            )
