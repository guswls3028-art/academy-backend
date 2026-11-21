"""Course-scoped assessment reports. Scores always come from confirmed first attempts."""
from collections import defaultdict
from statistics import mean

from django.db.models import F, Q
from rest_framework.exceptions import NotFound

from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam, ExamQuestion
from apps.domains.results.models import ResultItem
from apps.domains.results.services.exam_analysis_metrics import load_exam_analysis
from apps.domains.results.services.omr_subjective_completion import pending_omr_result_ids
from apps.domains.students.services.dawon_directory_search import school_of
from apps.domains.students.models import DawonExamReport, DawonExamReview, DawonReportHomework


def confirmed_scores(analysis):
    pending = pending_omr_result_ids(list(analysis.results.values()))
    return {
        key: score.initial for key, score in analysis.score_by_enrollment.items()
        if score.status == "DONE" and analysis.results[key].pk not in pending
    }


def question_marks(exam, analysis, confirmed):
    questions = ExamQuestion.objects.filter(sheet__exam_id=exam.effective_structure_exam_id,
                                            sheet__exam__tenant_id=exam.tenant_id).order_by("number")
    attempts = {}
    for key, score in confirmed.items():
        result = analysis.results[key]
        if result.attempt_id == score.attempt_id:
            attempts[result.pk] = key
    marks = defaultdict(dict)
    for result_id, question_id, correct in ResultItem.objects.filter(
        result_id__in=attempts, question__sheet__exam_id=exam.effective_structure_exam_id
    ).values_list("result_id", "question_id", "is_correct"):
        marks[question_id][attempts[result_id]] = correct
    for question in questions:
        values = marks[question.pk]
        metadata = question.region_meta or {}
        yield {"number": question.number, "topic": metadata.get("topic", "") if isinstance(metadata, dict) else ""}, values


def question_rows(exam, analysis, confirmed, enrollment_id):
    return [{**question, "correct": values.get(enrollment_id),
             "correct_rate": 100 * sum(values.values()) / len(values) if values else None}
            for question, values in question_marks(exam, analysis, confirmed)]


def report_for_student(*, tenant, exam, session, enrollment, analysis):
    if not session:
        return None
    course_id = enrollment.lecture_id
    scope = DawonExamReport.objects.filter(
        session__lecture_id=course_id, session__lecture__tenant=tenant,
        exam__tenant=tenant, exam__is_active=True, exam__student_results_published=True,
        exam__sessions=F("session_id"), exam__exam_enrollments__enrollment=enrollment,
    ).select_related("session__lecture", "exam")
    primary = scope.filter(exam=exam, session=session).first()
    if primary is None:
        return None
    definitions = scope.filter(kind=primary.kind).filter(
        Q(session=session, title=primary.title) | Q(series=primary.series, sequence__lte=primary.sequence)
    ).order_by("sequence", "exam_id")
    sections, trend = [], []
    for definition in definitions:
        related = definition.exam
        own_analysis = analysis if related.pk == exam.pk else load_exam_analysis(
            exam=related, tenant=tenant, lecture_id=course_id, include_trends=False)
        confirmed = confirmed_scores(own_analysis)
        own = confirmed.get(enrollment.pk)
        if own is None:
            continue
        scores = [score.total_score for score in confirmed.values()]
        percentile = 100 * sum(value <= own.total_score for value in scores) / len(scores)
        point = {"score": own.total_score, "maximum": own.max_score, "average": mean(scores), "highest": max(scores),
                 "percentile": percentile if related.student_rank_published else None}
        if definition.series == primary.series and definition.sequence <= primary.sequence:
            trend.append({"label": definition.section, "exam_id": related.pk, **point})
        if definition.session_id == session.pk and definition.title == primary.title:
            sections.append({"exam_id": related.pk, "title": definition.section, "scope": definition.scope,
                             "questions": question_rows(related, own_analysis, confirmed, enrollment.pk), **point})
    if not sections:
        return None
    homework = DawonReportHomework.objects.filter(report=primary, enrollment=enrollment).order_by("pk").values(
        "title", "completion", "wrong", "unfinished")
    return {"title": primary.title, "kind": primary.kind, "course_title": session.lecture.title,
            "lesson_title": session.title, "date": session.date, "scope": primary.scope,
            "sections": sections, "trend": trend, "homework": list(homework),
            "next_homework": primary.next_homework, "clinic": primary.clinic}


def exam_for_management(tenant, exam_id, course_id):
    exam = Exam.objects.filter(tenant=tenant, pk=exam_id, is_active=True, exam_type=Exam.ExamType.REGULAR,
                               sessions__lecture_id=course_id, sessions__lecture__tenant=tenant).first()
    if exam is None:
        raise NotFound()
    return exam


def admin_exam_detail(tenant, exam, course_id, reviewer):
    analysis = load_exam_analysis(exam=exam, tenant=tenant, lecture_id=course_id, include_trends=False)
    confirmed = confirmed_scores(analysis)
    values = [score.total_score for score in confirmed.values()]
    distribution = [0] * 5
    students = {e.pk: e.student for e in Enrollment.objects.filter(pk__in=analysis.candidates, tenant=tenant,
                     lecture_id=course_id, student__tenant=tenant).select_related("student").only(
                         "pk", "student__id", "student__school_type", "student__high_school", "student__middle_school",
                         "student__elementary_school", "student__grade")}
    roster = []
    for key, candidate in analysis.candidates.items():
        score = confirmed.get(key)
        row = analysis.score_by_enrollment.get(key)
        if score and score.max_score:
            # Intervals [0,20), [20,40), [40,60), [60,80), [80,100].
            index = min(4, int(5 * score.total_score / score.max_score))
            distribution[index] += 1
        roster.append({"id": key, "student_id": students[key].pk, "name": candidate.student_name,
                       "school": school_of(students[key]), "grade": students[key].grade,
                       "score": score.total_score if score else None,
                       "maximum": score.max_score if score else None,
                       "status": "채점 완료" if score else "미응시" if row is None or row.status == "NOT_SUBMITTED" else "채점 대기"})
    roster.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["name"], r["id"]))
    questions = [{**question, "graded_ids": sorted(values),
                  "correct_ids": sorted(key for key, correct in values.items() if correct)}
                 for question, values in question_marks(exam, analysis, confirmed)]
    return {"title": exam.title, "questions": questions, "assigned": len(roster), "graded": len(confirmed),
            "top_reference_percent": analysis.insights["score_summary"].get("top_reference_percent"),
            "average": mean(values) if values else None, "highest": max(values) if values else None,
            "lowest": min(values) if values else None, "distribution": distribution, "roster": roster,
            "reviewed": DawonExamReview.objects.filter(exam=exam, lecture_id=course_id, reviewer=reviewer).exists()}
