"""
성적 탭 PDF 출력 테스트용 시드 데이터 생성
실행: python manage.py shell < apps/domains/scores_test_seed.py
"""
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", os.environ.get("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.prod"))
django.setup()

from django.utils import timezone
from apps.core.models import Tenant
from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.results.models import Result

tenant = Tenant.objects.get(id=1)

# 1) 수강생이 있는 강의 찾기
lecture = None
for lec in Lecture.objects.filter(tenant=tenant):
    cnt = Enrollment.objects.filter(lecture=lec, tenant=tenant, status="ACTIVE", student__deleted_at__isnull=True).count()
    if cnt > 0:
        lecture = lec
        break
if not lecture:
    print("ERROR: tenant 1 has no lecture with active students")
    exit()

session = Session.objects.filter(lecture=lecture, order=1).first()
if not session:
    session = Session.objects.filter(lecture=lecture).order_by("order").first()
if not session:
    print("ERROR: 강의에 차시가 없습니다")
    exit()

print(f"강의: {lecture.title} (id={lecture.id})")
print(f"차시: {session.title or f'{session.order}차시'} (id={session.id})")

# 2) 수강생 조회
enrollments = list(
    Enrollment.objects.filter(tenant=tenant, lecture=lecture, status="ACTIVE")
    .filter(student__deleted_at__isnull=True)
    .select_related("student")
    .order_by("id")
)
print(f"수강생: {len(enrollments)}명")
if len(enrollments) == 0:
    print("ERROR: 활성 수강생이 없습니다")
    exit()

# SessionEnrollment 보장
for en in enrollments:
    SessionEnrollment.objects.get_or_create(
        tenant=tenant, session=session, enrollment=en
    )

# 3) 시험 2개 생성
exam1, _ = Exam.objects.get_or_create(
    title="중간고사",
    exam_type="regular",
    defaults={"max_score": 100, "pass_score": 70, "status": "CLOSED", "is_active": True},
)
exam1.sessions.add(session)

exam2, _ = Exam.objects.get_or_create(
    title="쪽지시험",
    exam_type="regular",
    defaults={"max_score": 50, "pass_score": 30, "status": "CLOSED", "is_active": True},
)
exam2.sessions.add(session)
print(f"시험: {exam1.title}(id={exam1.id}), {exam2.title}(id={exam2.id})")

# 4) 과제 3개 생성
hw1, _ = Homework.objects.get_or_create(
    session=session, title="단어테스트",
    defaults={"homework_type": "regular", "status": "CLOSED"},
)
hw2, _ = Homework.objects.get_or_create(
    session=session, title="문법정리",
    defaults={"homework_type": "regular", "status": "CLOSED"},
)
hw3, _ = Homework.objects.get_or_create(
    session=session, title="독해과제",
    defaults={"homework_type": "regular", "status": "CLOSED"},
)
print(f"과제: {hw1.title}, {hw2.title}, {hw3.title}")

# 5) 대상자 등록 + 성적 생성 (다채로운 합격/불합격)
import random
random.seed(42)

# 점수 패턴: [시험1, 시험2, 과제1, 과제2, 과제3]
# None = 미응시/미제출
PATTERNS = [
    # 전과목 통과
    (92, 45, 90, 85, 95),
    (78, 38, 80, 70, 88),
    (85, 42, 75, 90, 82),
    # 시험1만 불합격
    (55, 40, 85, 90, 78),
    (65, 35, 70, 80, 75),
    # 시험2만 불합격
    (80, 22, 88, 75, 90),
    # 과제 미제출 포함
    (90, 48, None, 85, 70),
    (72, 32, 60, None, 80),
    # 전부 불합격
    (45, 18, 30, 25, 40),
    # 미응시 포함
    (None, 40, 80, 75, None),
    (88, None, 90, 85, 92),
    # 커트라인 근접
    (70, 30, 65, 72, 68),
    (69, 29, 80, 78, 85),
]

for i, en in enumerate(enrollments):
    pattern = PATTERNS[i % len(PATTERNS)]
    e1_score, e2_score, h1_score, h2_score, h3_score = pattern

    # ExamEnrollment
    ExamEnrollment.objects.get_or_create(exam=exam1, enrollment=en)
    ExamEnrollment.objects.get_or_create(exam=exam2, enrollment=en)

    # HomeworkAssignment
    HomeworkAssignment.objects.get_or_create(
        tenant=tenant, homework=hw1, session=session,
        defaults={"enrollment_id": en.id},
    )
    HomeworkAssignment.objects.get_or_create(
        tenant=tenant, homework=hw2, session=session,
        defaults={"enrollment_id": en.id},
    )
    HomeworkAssignment.objects.get_or_create(
        tenant=tenant, homework=hw3, session=session,
        defaults={"enrollment_id": en.id},
    )

    # 시험 Result
    for exam, score in [(exam1, e1_score), (exam2, e2_score)]:
        if score is not None:
            Result.objects.update_or_create(
                target_type="exam",
                target_id=exam.id,
                enrollment_id=en.id,
                defaults={
                    "total_score": score,
                    "max_score": exam.max_score,
                    "objective_score": score,
                    "submitted_at": timezone.now(),
                },
            )

    # HomeworkScore
    for hw, score in [(hw1, h1_score), (hw2, h2_score), (hw3, h3_score)]:
        if score is not None:
            HomeworkScore.objects.update_or_create(
                enrollment_id=en.id,
                session=session,
                homework=hw,
                defaults={
                    "score": score,
                    "max_score": 100,
                    "passed": score >= 60,
                },
            )
        else:
            HomeworkScore.objects.update_or_create(
                enrollment_id=en.id,
                session=session,
                homework=hw,
                defaults={
                    "score": None,
                    "max_score": 100,
                    "passed": False,
                    "meta": {"status": "NOT_SUBMITTED"},
                },
            )

    name = en.student.name if en.student else f"학생{en.id}"
    status = "통과" if all(
        s is not None and (
            s >= (70 if idx == 0 else 30 if idx == 1 else 60)
        )
        for idx, s in enumerate(pattern)
    ) else "미달"
    print(f"  {name}: 시험1={e1_score or '-'}, 시험2={e2_score or '-'}, 과제1={h1_score or '-'}, 과제2={h2_score or '-'}, 과제3={h3_score or '-'} → {status}")

print(f"\n✅ 완료: 시험 2개, 과제 3개, {len(enrollments)}명 성적 생성")
