from itertools import groupby
from operator import itemgetter

from django.conf import settings
from django.db.models import F
from django.utils import timezone


def assign_cycle_status(rows, today):
    """Rows are newest first, with link creation order breaking same-day ties."""
    started = False
    vacation_started = False
    exam_schools = set()
    for row in rows:
        if row["start_date"] and row["start_date"] > today:
            row["cycle_status"] = "upcoming" if row.get("is_active", True) else "past"
            continue
        vacation = row["period"] == "vacation"
        superseded = started if vacation else vacation_started or row["school"] in exam_schools
        row["cycle_status"] = "past" if not row.get("is_active", True) or superseded or (row["end_date"] and row["end_date"] < today) else "current"
        started = True
        if vacation:
            vacation_started = True
        else:
            exam_schools.add(row["school"])
    return rows


def class_group_cycles(tenant, *, group_id=None):
    if not getattr(settings, "DAWON_REVIEW_CLASS_GROUPS", False) or (tenant.pk, tenant.code) != (719, "dawon"):
        return {}
    from apps.domains.students.models import ClassGroupLecture

    links = ClassGroupLecture.objects.filter(group__tenant=tenant, lecture__tenant=tenant)
    if group_id is not None:
        links = links.filter(group_id=group_id)
    rows = links.order_by("group_id", F("lecture__start_date").desc(nulls_last=True), "-pk").values(
        "group_id", "lecture_id", "school", "period", start_date=F("lecture__start_date"), end_date=F("lecture__end_date"), is_active=F("lecture__is_active")
    )
    today = timezone.localdate()
    result = {}
    for group, group_rows in groupby(rows, key=itemgetter("group_id")):
        for row in assign_cycle_status(list(group_rows), today):
            result[row["lecture_id"]] = {"class_group_id": group, "cycle_status": row["cycle_status"]}
    return result
