"""
배포 전 데이터 정합성 탐지 스크립트 (P0/P1 버그 수정 전 기존 데이터 검증)

실행: python manage.py shell < scripts/check_data_integrity.py
"""
import json
from django.db.models import Count, Q, F, Max

print("=" * 70)
print("데이터 정합성 탐지 보고서")
print("=" * 70)

# ─── 1. ExamAttempt: is_representative=True 중복 ───
from apps.domains.results.models import ExamAttempt

dupes = (
    ExamAttempt.objects
    .filter(is_representative=True)
    .values("exam_id", "enrollment_id")
    .annotate(cnt=Count("id"))
    .filter(cnt__gt=1)
)
dupe_list = list(dupes)
print(f"\n[1] ExamAttempt is_representative=True 중복: {len(dupe_list)}건")
for d in dupe_list[:10]:
    print(f"    exam={d['exam_id']} enrollment={d['enrollment_id']} count={d['cnt']}")

# ─── 2. ExamAttempt: is_representative=True 0개 ───
all_pairs = (
    ExamAttempt.objects
    .values("exam_id", "enrollment_id")
    .annotate(
        total=Count("id"),
        rep_count=Count("id", filter=Q(is_representative=True)),
    )
    .filter(rep_count=0)
)
no_rep_list = list(all_pairs)
print(f"\n[2] ExamAttempt is_representative=True 0개: {len(no_rep_list)}건")
for d in no_rep_list[:10]:
    print(f"    exam={d['exam_id']} enrollment={d['enrollment_id']} total={d['total']}")

# ─── 3. ExamAttempt: submission_id 중복 ───
sub_dupes = (
    ExamAttempt.objects
    .filter(submission_id__isnull=False)
    .values("submission_id")
    .annotate(cnt=Count("id"))
    .filter(cnt__gt=1)
)
sub_dupe_list = list(sub_dupes)
print(f"\n[3] ExamAttempt submission_id 중복: {len(sub_dupe_list)}건")
for d in sub_dupe_list[:10]:
    print(f"    submission_id={d['submission_id']} count={d['cnt']}")

# ─── 4. Exam: max_attempts=0 ───
from apps.domains.exams.models import Exam

bad_attempts = Exam.objects.filter(max_attempts=0).count()
print(f"\n[4] Exam max_attempts=0: {bad_attempts}건")
if bad_attempts:
    for e in Exam.objects.filter(max_attempts=0)[:5]:
        print(f"    id={e.id} title='{e.title}'")

# ─── 5. Exam: pass_score > max_score ───
bad_pass = Exam.objects.filter(pass_score__gt=F("max_score")).count()
print(f"\n[5] Exam pass_score > max_score: {bad_pass}건")
if bad_pass:
    for e in Exam.objects.filter(pass_score__gt=F("max_score"))[:5]:
        print(f"    id={e.id} pass_score={e.pass_score} max_score={e.max_score}")

# ─── 6. Exam: open_at >= close_at ───
bad_dates = Exam.objects.filter(
    open_at__isnull=False, close_at__isnull=False,
    open_at__gte=F("close_at"),
).count()
print(f"\n[6] Exam open_at >= close_at: {bad_dates}건")

# ─── 7. HomeworkScore: score > max_score ───
from apps.domains.homework_results.models import HomeworkScore

bad_hw_score = HomeworkScore.objects.filter(
    score__isnull=False, max_score__isnull=False,
    score__gt=F("max_score"), max_score__gt=0,
).count()
print(f"\n[7] HomeworkScore score > max_score: {bad_hw_score}건")
if bad_hw_score:
    for s in HomeworkScore.objects.filter(
        score__isnull=False, max_score__isnull=False,
        score__gt=F("max_score"), max_score__gt=0,
    )[:5]:
        print(f"    id={s.id} score={s.score} max_score={s.max_score}")

# ─── 8. ClinicLink: source_type/source_id 둘 다 NULL + unresolved 중복 ───
from apps.domains.progress.models import ClinicLink

legacy_unresolved = (
    ClinicLink.objects
    .filter(source_type__isnull=True, source_id__isnull=True, resolved_at__isnull=True)
    .values("enrollment_id", "session_id")
    .annotate(cnt=Count("id"))
    .filter(cnt__gt=1)
)
legacy_list = list(legacy_unresolved)
print(f"\n[8] ClinicLink 레거시(NULL source) 미해소 중복: {len(legacy_list)}건")
for d in legacy_list[:10]:
    print(f"    enrollment={d['enrollment_id']} session={d['session_id']} count={d['cnt']}")

# ─── 9. ExamResult: manual_overrides에서 max_score 왜곡 탐지 ───
from apps.domains.results.models import ExamResult

# manual_overrides가 있는 결과 중 문항별 score == max_score인 경우 (잠재적 왜곡)
results_with_overrides = ExamResult.objects.exclude(manual_overrides={}).exclude(manual_overrides__isnull=True)
suspicious_count = 0
suspicious_examples = []
for r in results_with_overrides[:100]:
    overrides = r.manual_overrides
    if isinstance(overrides, dict):
        for qid, info in overrides.items():
            if isinstance(info, dict):
                s = info.get("score", 0)
                ms = info.get("max_score")
                if ms is None:
                    # max_score 필드 없음 = 기존 왜곡 패턴
                    suspicious_count += 1
                    if len(suspicious_examples) < 3:
                        suspicious_examples.append(f"result_id={r.id} qid={qid} score={s}")
                    break
print(f"\n[9] ExamResult manual_overrides에 max_score 없음 (왜곡 가능): {suspicious_count}건")
for ex in suspicious_examples:
    print(f"    {ex}")

print("\n" + "=" * 70)
print("탐지 완료")
print("=" * 70)
