"""
Data integrity check for P0/P1 bug fixes.
Usage: python manage.py check_integrity
"""
from django.core.management.base import BaseCommand
from django.db.models import Count, Q, F, Max


class Command(BaseCommand):
    help = "Check data integrity for exam/homework domains before migration"

    def handle(self, *args, **options):
        self.stdout.write("=" * 70)
        self.stdout.write("DATA INTEGRITY CHECK")
        self.stdout.write("=" * 70)

        from apps.domains.results.models import ExamAttempt

        # 1. is_representative=True duplicates
        dupes = list(
            ExamAttempt.objects
            .filter(is_representative=True)
            .values("exam_id", "enrollment_id")
            .annotate(cnt=Count("id"))
            .filter(cnt__gt=1)
        )
        self.stdout.write(f"\n[1] ExamAttempt is_representative=True DUPE: {len(dupes)}")
        for d in dupes[:10]:
            self.stdout.write(f"    exam={d['exam_id']} enrollment={d['enrollment_id']} count={d['cnt']}")

        # 2. is_representative=True zero
        no_rep = list(
            ExamAttempt.objects
            .values("exam_id", "enrollment_id")
            .annotate(total=Count("id"), rep_count=Count("id", filter=Q(is_representative=True)))
            .filter(rep_count=0)
        )
        self.stdout.write(f"\n[2] ExamAttempt is_representative=True ZERO: {len(no_rep)}")
        for d in no_rep[:10]:
            self.stdout.write(f"    exam={d['exam_id']} enrollment={d['enrollment_id']} total={d['total']}")

        # 3. submission_id duplicates
        sub_dupes = list(
            ExamAttempt.objects
            .filter(submission_id__isnull=False)
            .values("submission_id")
            .annotate(cnt=Count("id"))
            .filter(cnt__gt=1)
        )
        self.stdout.write(f"\n[3] ExamAttempt submission_id DUPE: {len(sub_dupes)}")
        for d in sub_dupes[:10]:
            self.stdout.write(f"    submission_id={d['submission_id']} count={d['cnt']}")

        # 4. Exam max_attempts=0
        from apps.domains.exams.models import Exam
        bad_attempts = Exam.objects.filter(max_attempts=0).count()
        self.stdout.write(f"\n[4] Exam max_attempts=0: {bad_attempts}")

        # 5. Exam pass_score > max_score
        bad_pass = Exam.objects.filter(pass_score__gt=F("max_score")).count()
        self.stdout.write(f"\n[5] Exam pass_score > max_score: {bad_pass}")

        # 6. Exam open_at >= close_at
        bad_dates = Exam.objects.filter(
            open_at__isnull=False, close_at__isnull=False,
            open_at__gte=F("close_at"),
        ).count()
        self.stdout.write(f"\n[6] Exam open_at >= close_at: {bad_dates}")

        # 7. HomeworkScore score > max_score
        from apps.domains.homework_results.models import HomeworkScore
        bad_hw = HomeworkScore.objects.filter(
            score__isnull=False, max_score__isnull=False,
            score__gt=F("max_score"), max_score__gt=0,
        ).count()
        self.stdout.write(f"\n[7] HomeworkScore score > max_score: {bad_hw}")

        # 8. ClinicLink legacy NULL source unresolved dupes
        from apps.domains.progress.models import ClinicLink
        legacy_dupes = list(
            ClinicLink.objects
            .filter(source_type__isnull=True, source_id__isnull=True, resolved_at__isnull=True)
            .values("enrollment_id", "session_id")
            .annotate(cnt=Count("id"))
            .filter(cnt__gt=1)
        )
        self.stdout.write(f"\n[8] ClinicLink legacy NULL source unresolved DUPE: {len(legacy_dupes)}")
        for d in legacy_dupes[:10]:
            self.stdout.write(f"    enrollment={d['enrollment_id']} session={d['session_id']} count={d['cnt']}")

        # 9. ExamResult manual_overrides missing max_score key
        from apps.domains.results.models import ExamResult
        results = ExamResult.objects.exclude(manual_overrides={}).exclude(manual_overrides__isnull=True)
        suspicious = 0
        for r in results[:200]:
            overrides = r.manual_overrides
            if isinstance(overrides, dict):
                for qid, info in overrides.items():
                    if isinstance(info, dict) and "max_score" not in info:
                        suspicious += 1
                        break
        self.stdout.write(f"\n[9] ExamResult manual_overrides missing max_score: {suspicious}")

        self.stdout.write("\n" + "=" * 70)

        # Summary for migration safety
        blockers = []
        if dupes:
            blockers.append(f"[1] {len(dupes)} is_representative duplicates - MUST FIX before migration")
        if sub_dupes:
            blockers.append(f"[3] {len(sub_dupes)} submission_id duplicates - MUST FIX before migration")
        if bad_attempts:
            blockers.append(f"[4] {bad_attempts} exams with max_attempts=0 - MUST FIX before migration")
        if bad_pass:
            blockers.append(f"[5] {bad_pass} exams with pass_score > max_score - MUST FIX before migration")

        if blockers:
            self.stdout.write("\nBLOCKERS (fix before migrate):")
            for b in blockers:
                self.stdout.write(f"  !! {b}")
        else:
            self.stdout.write("\nNO BLOCKERS - safe to migrate")

        self.stdout.write("=" * 70)
