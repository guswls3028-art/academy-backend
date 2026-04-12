"""
Data integrity snapshot for before/after deployment comparison.
Usage: python manage.py shell -c "exec(open('scripts/integrity_snapshot.py').read())"
"""
import json
from datetime import datetime

snapshot = {
    "timestamp": datetime.now().isoformat(),
    "checks": {}
}

from django.db.models import Count, Q, F

# ExamAttempt stats
from apps.domains.results.models import ExamAttempt
snapshot["checks"]["exam_attempt_total"] = ExamAttempt.objects.count()
snapshot["checks"]["exam_attempt_representative"] = ExamAttempt.objects.filter(is_representative=True).count()
snapshot["checks"]["exam_attempt_null_submission"] = ExamAttempt.objects.filter(submission_id__isnull=True).count()

rep_dupes = ExamAttempt.objects.filter(is_representative=True).values("exam_id", "enrollment_id").annotate(cnt=Count("id")).filter(cnt__gt=1).count()
snapshot["checks"]["representative_duplicates"] = rep_dupes

no_rep = ExamAttempt.objects.values("exam_id", "enrollment_id").annotate(total=Count("id"), rep=Count("id", filter=Q(is_representative=True))).filter(rep=0).count()
snapshot["checks"]["no_representative"] = no_rep

sub_dupes = ExamAttempt.objects.filter(submission_id__isnull=False).values("submission_id").annotate(cnt=Count("id")).filter(cnt__gt=1).count()
snapshot["checks"]["submission_id_duplicates"] = sub_dupes

# Exam stats
from apps.domains.exams.models import Exam
snapshot["checks"]["exam_total"] = Exam.objects.count()
snapshot["checks"]["exam_bad_attempts"] = Exam.objects.filter(max_attempts=0).count()
snapshot["checks"]["exam_bad_pass_score"] = Exam.objects.filter(pass_score__gt=F("max_score")).count()

# HomeworkScore stats
from apps.domains.homework_results.models import HomeworkScore
snapshot["checks"]["homework_score_total"] = HomeworkScore.objects.count()
snapshot["checks"]["homework_score_exceeds_max"] = HomeworkScore.objects.filter(score__isnull=False, max_score__isnull=False, score__gt=F("max_score"), max_score__gt=0).count()

# ClinicLink stats
from apps.domains.progress.models import ClinicLink
snapshot["checks"]["clinic_link_total"] = ClinicLink.objects.count()
snapshot["checks"]["clinic_link_unresolved"] = ClinicLink.objects.filter(resolved_at__isnull=True).count()

print(json.dumps(snapshot, indent=2, ensure_ascii=False))
