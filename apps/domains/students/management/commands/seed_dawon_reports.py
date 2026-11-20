"""Seed fictitious teaching records in the isolated, public Dawon demonstration only."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.core.models import Tenant, TenantMembership
from apps.domains.clinic.dawon_demo import is_demo_tenant
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import Exam, ExamEnrollment, ExamQuestion, Sheet
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models.homework import Homework
from apps.domains.lectures.models import Session
from apps.domains.results.models import Result, ResultItem
from apps.domains.students.models import ClassGroupLecture, DawonExamReport, DawonReportHomework, Student

MARK = "dawon-demo:assessment-report:1"
TOPICS = ("함수의 정의", "정의역과 치역", "합성함수", "역함수", "그래프의 이동",
          "유리식", "유리함수의 점근선", "무리함수", "함수의 교점", "조건의 해석")
NAMES = ("가온", "나래", "다빈", "라온", "서우", "도윤", "예린", "은솔")


def sample_roster(tenant, group, learner):
    result = [learner]
    for n in range(32):
        name = ("김", "윤", "서", "정")[n // 8] + NAMES[n % 8]
        account, new = get_user_model()._base_manager.get_or_create(
            tenant=tenant, username=f"t719_demo-report-student-{n:02}", defaults={"name": name, "password": make_password(None)})
        if not new and (account.has_usable_password() or account.is_staff or account.is_superuser):
            raise CommandError("Reserved demonstration identity already in use.")
        TenantMembership._base_manager.get_or_create(tenant=tenant, user=account, role="student", defaults={"is_active": True})
        student, _ = Student._base_manager.get_or_create(tenant=tenant, user=account, defaults={
            "name": name, "ps_number": f"DW-R-{n:02}", "omr_code": f"709900{n:02}", "school_type": "HIGH",
            "grade": 1, "high_school": learner.high_school if n % 3 else "예시고", "parent_phone": "",
        })
        if student.ps_number != f"DW-R-{n:02}":
            raise CommandError("Unexpected existing sample student.")
        result.append(student)
    group.students.add(*result)
    return result


def make_session(lecture, title, day):
    existing = Session._base_manager.filter(lecture=lecture, title=title).first()
    if existing:
        if not DawonExamReport.objects.filter(session=existing).exists():
            raise CommandError("Existing lesson already uses the report sample title.")
        return existing
    last = Session._base_manager.filter(lecture=lecture).aggregate(last=Max("order"))["last"] or 0
    return Session._base_manager.create(lecture=lecture, order=last + 1, title=title, date=day)


def assessment(tenant, session, roster, *, kind, title, section, series, sequence, count, scope):
    exam, new = Exam._base_manager.get_or_create(tenant=tenant, title=f"{title} · {section}", defaults={
        "max_score": 100, "description": MARK, "student_results_published": True})
    if not new:
        if exam.description != MARK or not DawonExamReport.objects.filter(exam=exam, session=session, kind=kind).exists():
            raise CommandError("Existing assessment conflicts with demonstration data.")
        return
    exam.sessions.add(session)
    report = DawonExamReport.objects.create(exam=exam, session=session, kind=kind, section=section, title=title,
        series=series, sequence=sequence, scope=scope, next_homework="틀린 문항을 다시 풀고 풀이 과정을 정리해 오세요.",
        clinic="함수 단원 오답을 준비해 클리닉에 참여할 수 있습니다.")
    sheet = Sheet._base_manager.create(exam=exam, total_questions=count, choice_count=count)
    questions = [ExamQuestion._base_manager.create(sheet=sheet, number=q+1, score=100/count,
                  region_meta={"topic": TOPICS[q % len(TOPICS)]}) for q in range(count)]
    for index, enrollment in enumerate(roster):
        SessionEnrollment._base_manager.get_or_create(tenant=tenant, session=session, enrollment=enrollment)
        ExamEnrollment.objects.create(exam=exam, enrollment=enrollment)
        hits = max(0, min(count, round(count * (.95 - ((index * 7 + sequence * 9) % 40)/100))))
        if index == 0:
            hits = max(1, count - sequence - (2 if kind == "school_mock" else 0))
        marks = [((q + index + sequence) % count) < hits for q in range(count)]
        result = Result.objects.create(target_type="exam", target_id=exam.pk, enrollment=enrollment,
            total_score=hits * 100/count, objective_score=hits * 100/count, max_score=100, submitted_at=timezone.now())
        ResultItem.objects.bulk_create([ResultItem(result=result, question=q, is_correct=hit, score=q.score if hit else 0,
                 max_score=q.score, source="manual") for q, hit in zip(questions, marks)])
        for unit in range(1, min(sequence + 2, 5)):
            DawonReportHomework.objects.create(report=report, enrollment=enrollment, title=f"{unit}차시 · {TOPICS[unit-1]}",
                completion=75 if unit == 2 else 100, wrong=[2, 5] if unit == 1 else [3], unfinished=[7, 8] if unit == 2 else [])


class Command(BaseCommand):
    @transaction.atomic
    def handle(self, *args, **options):
        tenant = Tenant._base_manager.select_for_update().get(pk=719, code="dawon")
        if not is_demo_tenant(tenant):
            raise CommandError("Dedicated Dawon review environment required.")
        learner = Student._base_manager.get(tenant=tenant, pk=2, name="다원 수업 검수 학생 2")
        vacation = ClassGroupLecture.objects.select_related("lecture", "group").get(
            group__tenant=tenant, group__name="고1 HYPER 연합반", lecture__tenant=tenant, lecture__title="HYPER 방학 수학Ⅱ")
        school = ClassGroupLecture.objects.select_related("lecture").get(
            group=vacation.group, school=learner.high_school, period="midterm", lecture__tenant=tenant)
        students = sample_roster(tenant, vacation.group, learner)
        today = timezone.localdate()
        for link, groups in [
            (vacation, [("level_pair", "빈출·심화 리포트", [("빈출", 10), ("심화", 5)], 2),
                        ("cycle", "공통 회차 리포트", [("공통", 20)], 3)]),
            (school, [("school_mock", "학교별 모의시험 리포트", [("모의시험", 10)], 3)])]:
            roster = [Enrollment._base_manager.get_or_create(tenant=tenant, lecture=link.lecture, student=s)[0]
                      for s in students if not link.school or s.high_school == link.school]
            for kind, title, parts, rounds in groups:
                for sequence in range(1, rounds + 1):
                    day = today - timedelta(days=(rounds - sequence) * 4 + (2 if kind == "level_pair" else 1))
                    session = make_session(link.lecture, f"{title} {sequence}차시", day)
                    for label, count in parts:
                        assessment(tenant, session, roster, kind=kind, title=title, section=f"{label} {sequence}회",
                            series=label, sequence=sequence, count=count, scope="공통수학 · 함수와 그래프")
                    if kind == "school_mock" and sequence <= 2:
                        hw, added = Homework._base_manager.get_or_create(tenant=tenant, session=session, title=f"함수 오답연습 {sequence}",
                            defaults={"status": "OPEN", "meta": {"due_date": (day+timedelta(days=1)).isoformat(), "dawon_fixture": MARK}})
                        if not added and (hw.meta or {}).get("dawon_fixture") != MARK:
                            raise CommandError("Unexpected existing assignment.")
                        for enrollment in roster[:3]:
                            HomeworkAssignment.objects.get_or_create(tenant=tenant, session=session, homework=hw, enrollment=enrollment)
        self.stdout.write("Dawon report examples and class enrollments verified.")
