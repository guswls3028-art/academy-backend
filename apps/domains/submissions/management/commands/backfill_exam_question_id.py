# apps/domains/submissions/management/commands/backfill_exam_question_id.py
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.exams.models import ExamQuestion


class Command(BaseCommand):
    """
    legacy question_number(number) → exam_question_id(ExamQuestion.id) 백필

    사용 예)
    python manage.py backfill_exam_question_id --exam-id 10
    python manage.py backfill_exam_question_id --dry-run
    """

    help = "Backfill SubmissionAnswer.exam_question_id from legacy question_number."

    def add_arguments(self, parser):
        parser.add_argument("--exam-id", type=int)
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **opts):
        exam_id = opts.get("exam_id")
        dry_run = bool(opts.get("dry_run", False))

        subs = Submission.objects.filter(target_type=Submission.TargetType.EXAM)
        if exam_id:
            subs = subs.filter(target_id=int(exam_id))

        answers = (
            SubmissionAnswer.objects
            .filter(submission__in=subs, exam_question_id__isnull=True)
            .exclude(question_number__isnull=True)
            .select_related("submission")
        )

        cache: dict[int, dict[int, int]] = {}
        updated = 0

        for sa in answers:
            exid = int(sa.submission.target_id)

            # exam_id별 number→ExamQuestion.id 캐시
            if exid not in cache:
                cache[exid] = {
                    int(q.number): int(q.id)
                    for q in ExamQuestion.objects.filter(sheet__exam_id=exid)
                }

            qnum = int(sa.question_number) if sa.question_number is not None else None
            if not qnum:
                continue

            qid = cache[exid].get(qnum)
            if not qid:
                continue

            if not dry_run:
                sa.exam_question_id = int(qid)
                sa.save(update_fields=["exam_question_id", "updated_at"])

            updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill done. updated={updated}, dry_run={dry_run}"
            )
        )
