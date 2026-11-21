"""Course-scoped assessment reports. Scores always come from confirmed first attempts."""
from collections import defaultdict
from statistics import mean

from django.db.models import F, Q
from rest_framework.exceptions import NotFound
from rest_framework.fields import ChoiceField, IntegerField

from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam, ExamQuestion
from apps.domains.results.models import ResultItem
from apps.domains.results.services.exam_analysis_metrics import directory_exam_scores, load_exam_analysis
from apps.domains.results.services.omr_subjective_completion import pending_omr_result_ids
from apps.domains.students.services.dawon_directory_search import natural_name, school_of
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

def student_results(tenant, params):
    """One row per active class-course student; batch-load exam assignments and confirmed first attempts."""

    sort = ChoiceField(choices=("name", "recent", "decline", "improvement", "lowest")).run_validation(params.get("sort") or "name")
    status = ChoiceField(choices=("graded", "absent", "pending", "no_exam")).run_validation(params["status"]) if params.get("status") else None
    page = IntegerField(min_value=1, max_value=1000000).run_validation(params.get("page", 1))
    scope = Enrollment.objects.filter(tenant=tenant, status="ACTIVE", student__tenant=tenant, student__deleted_at__isnull=True,
                                      lecture__tenant=tenant, lecture__class_group_link__group__tenant=tenant)
    for key, field in (("group", "lecture__class_group_link__group_id"), ("lecture", "lecture_id")):
        if params.get(key):
            scope = scope.filter(**{field: IntegerField(min_value=1, max_value=2**63-1).run_validation(params[key])})
    if term := params.get("q", "").strip()[:100]:
        scope = scope.filter(student__name__icontains=term)
    learners, enrolled, course_ids = {}, {}, set()
    for eid, sid, name, cid, title, gid, group in scope.order_by("lecture__title", "pk").values_list(
        "pk", "student_id", "student__name", "lecture_id", "lecture__title", "lecture__class_group_link__group_id",
        "lecture__class_group_link__group__name",
    ):
        row = learners.setdefault(sid, dict(student_id=sid, name=name, courses=[], assessments=[]))
        row["courses"].append(dict(lecture_id=cid, course=title, group_id=gid, group=group))
        enrolled[eid] = (sid, cid)
        course_ids.add(cid)

    exams = list(Exam.objects.filter(tenant=tenant, is_active=True, exam_type=Exam.ExamType.REGULAR,
                                    sessions__lecture_id__in=course_ids).distinct().select_related("dawonexamreport"))
    assigned, scores, provisional = directory_exam_scores(tenant=tenant, exams=exams, enrollment_ids=enrolled)
    assigned_by_exam = defaultdict(list)
    for exam_id, eid in assigned:
        assigned_by_exam[exam_id].append(eid)
    dates = {}
    exam_by_id = {exam.pk: exam for exam in exams}
    for exam_id, session_id, cid, day in Exam.objects.filter(pk__in=exam_by_id, sessions__lecture_id__in=course_ids).order_by(
            "sessions__date", "sessions__pk").values_list("pk", "sessions__id", "sessions__lecture_id", "sessions__date"):
        definition = getattr(exam_by_id[exam_id], "dawonexamreport", None)
        if (exam_id, cid) not in dates or (definition and definition.session_id == session_id):
            dates[exam_id, cid] = (session_id, day)
    for exam in exams:
        definition = getattr(exam, "dawonexamreport", None)
        for eid in assigned_by_exam[exam.pk]:
            sid, cid = enrolled[eid]
            if (exam.pk, cid) not in dates:
                continue
            session_id, day = dates[exam.pk, cid]
            family = None
            if definition and definition.session_id == session_id:
                # Seeded labels include the display round ("심화 2회"); compare the section across rounds.
                section = definition.section.removesuffix(f" {definition.sequence}회")
                family = (definition.kind, definition.series, section) if definition.series and section else None
            elif exam.template_exam_id:
                family = ("template", exam.template_exam_id)
            key = (exam.pk, eid)
            source = scores.get(key)
            first = source.initial if source and source.status == "DONE" and key not in provisional else None
            state = "graded" if first else "absent" if not source or source.status == "NOT_SUBMITTED" else "pending"
            score, maximum = (first.total_score, first.max_score) if first else (None, None)
            point = dict(exam_id=exam.pk, title=exam.title, date=day, lecture_id=cid, status=state, score=score,
                         max_score=maximum, percent=100 * score / maximum if maximum and score is not None else None)
            comparison_max = maximum if first else exam.max_score
            learners[sid]["assessments"].append((point, (cid, family, comparison_max) if family and comparison_max else None))

    result = []
    for row in learners.values():
        points = sorted(row.pop("assessments"), key=lambda item: (
            item[0]["date"] is not None, item[0]["date"], item[0]["exam_id"]), reverse=True)
        latest, series = points[0] if points else (None, None)
        previous = next((point for point, key in points[1:] if series and series == key and latest["date"]
                         and point["date"] and point["date"] < latest["date"] and point["percent"] is not None), None)
        delta = latest["percent"] - previous["percent"] if previous and latest["percent"] is not None and previous["percent"] is not None else None
        row.update(latest=latest, previous=previous, delta_percent=delta, status=latest["status"] if latest else "no_exam")
        trend = params.get("trend")
        if trend == "decline" and (delta is None or delta >= 0):
            continue
        if trend == "improvement" and (delta is None or delta <= 0):
            continue
        if not status or row["status"] == status:
            result.append(row)
    result.sort(key=lambda row: (natural_name(row["name"]), row["student_id"]))
    if sort == "recent":
        result.sort(key=lambda row: str((row["latest"] or {}).get("date") or ""), reverse=True)
    elif sort == "lowest":
        result.sort(key=lambda row: (((row["latest"] or {}).get("percent") is None),
                                     (row["latest"] or {}).get("percent") or 0))
    elif sort in ("decline", "improvement"):
        direction = 1 if sort == "decline" else -1
        result.sort(key=lambda row: (row["delta_percent"] is None, direction * (row["delta_percent"] or 0)))
    start = (page - 1) * 25
    return {"count": len(result), "rows": result if params.get("export") == "1" else result[start:start+25]}
