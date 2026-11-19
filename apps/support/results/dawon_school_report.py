"""Dawon staff school-exam reporting for the selected academic term."""
import unicodedata
from decimal import Decimal
from itertools import groupby
from operator import attrgetter

from django.conf import settings


def is_dawon_school_report(tenant):
    return getattr(settings, "DAWON_REVIEW_CLASS_GROUPS", False) and (tenant.pk, tenant.code) == (719, "dawon")


def school_scope_at_submission(tenant, student, term, today):
    """Historical submissions cannot infer a past school from today's directory."""
    if not is_dawon_school_report(tenant) or term.academic_year != today.year - (today.month < 3):
        return {}
    school = {"HIGH": student.high_school, "MIDDLE": student.middle_school,
              "ELEMENTARY": student.elementary_school}.get(student.school_type)
    school = " ".join(unicodedata.normalize("NFKC", school or "").split())
    if not school or not student.grade:
        return {}
    return {"school_at_submission": school, "year_grade_at_submission": student.grade}


def school_report_cohorts(tenant, term, *, school=None, grade=None):
    from apps.support.results.school_reports import _current_scores, _term_scores

    rows = _term_scores(tenant, term).filter(student__tenant=tenant, student__deleted_at__isnull=True).only(
        "id", "student_id", "student__name", "subject", "score", "max_score", "created_at", "updated_at",
        "direct_entry", "school_at_submission", "year_grade_at_submission"
    ).select_related("student").order_by("student_id")
    groups = {}
    members = []
    for sid, history in groupby(rows.iterator(chunk_size=512), key=attrgetter("student_id")):
        included_groups = set()
        for row in _current_scores(history):
            scope = (row.school_at_submission or "", row.year_grade_at_submission)
            if not all(scope):
                scope = ("", None)
            entry = groups.setdefault(scope, {"students": 0, "subjects": {}})
            if scope not in included_groups:
                entry["students"] += 1
                included_groups.add(scope)
                if school is not None and (school, grade) == scope:
                    members.append({"id": sid, "name": row.student.name})
            if row.max_score <= 0:
                continue
            key = row.subject.casefold()
            item = entry["subjects"].setdefault(key, {"subject": row.subject, "count": 0, "total": Decimal(0)})
            item["count"] += 1
            item["total"] += row.score * 100 / row.max_score
    result = []
    for (name, level), entry in sorted(groups.items(), key=lambda pair: (not pair[0][0], pair[0][0], pair[0][1] or 0)):
        subjects = [
            {"subject": item["subject"], "count": item["count"], "average": round(item["total"] / item["count"], 2)}
            for _, item in sorted(entry["subjects"].items())
        ]
        result.append({"school": name, "grade": level, "student_count": entry["students"], "subjects": subjects})
    return {"cohorts": result, "students": sorted(members, key=lambda item: (item["name"], item["id"]))}
