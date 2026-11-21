"""Read models used by the director's student directory and enrollment picker."""
import re
import unicodedata
from collections import defaultdict

from django.db.models import Exists, OuterRef
from rest_framework.fields import IntegerField

from apps.core.models import TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.students.models import ClassGroup, Student


def compact(text):
    return "".join(unicodedata.normalize("NFKC", text or "").casefold().split())


def natural_name(text):
    return tuple(int(s) if i % 2 else s for i, s in enumerate(re.split(r"(\d+)", compact(text))))


def school_of(student):
    field = {"HIGH": "high_school", "MIDDLE": "middle_school", "ELEMENTARY": "elementary_school"}.get(student.school_type)
    return " ".join(unicodedata.normalize("NFKC", getattr(student, field, "") or "").split()) if field else ""


def student_row(student):
    return {"id": student.pk, "name": student.name, "school": school_of(student), "grade": student.grade,
            "phone": student.phone or "", "parent_phone": student.parent_phone or ""}


def active_students(qs, tenant):
    account = TenantMembership.objects.filter(tenant=tenant, user_id=OuterRef("user_id"),
                                              role="student", is_active=True, user__is_active=True)
    return qs.filter(tenant=tenant, deleted_at__isnull=True, is_managed=True).filter(Exists(account))


def student_page(qs, tenant, params, schools=()):
    offset = IntegerField(min_value=0).run_validation(params.get("offset", 0))
    limit = IntegerField(min_value=1, max_value=50).run_validation(params.get("limit", 50))
    if params.get("grade"):
        qs = qs.filter(grade=IntegerField(min_value=1, max_value=12).run_validation(params["grade"]))
    term = compact(params.get("search", ""))[:100]
    digits = re.sub(r"\D", "", term) if re.fullmatch(r"[\d()+.-]+", term) else ""
    allowed_schools = [s for s in schools if s]
    # School formatting and name normalization must match class eligibility and the Excel picker.
    # Retain ordering keys only, not the full roster and its contact details, while counting.
    matches = []
    scoped = active_students(qs, tenant)
    fields = ["id", "name", "school_type", "high_school", "middle_school", "elementary_school"]
    if digits:
        fields.extend(["phone", "parent_phone"])
    for student in scoped.only(*fields).iterator(chunk_size=250):
        school = school_of(student)
        if any(school != allowed for allowed in allowed_schools):
            continue
        if term and term not in compact(student.name) and term not in compact(school):
            if not digits or not any(digits in re.sub(r"\D", "", phone or "") for phone in (student.phone, student.parent_phone)):
                continue
        matches.append((natural_name(student.name), student.pk))
    matches.sort()
    ids = [pk for _, pk in matches[offset:offset + limit]]
    rows = {s.pk: student_row(s) for s in scoped.filter(pk__in=ids)}
    return {"count": len(matches), "results": [rows[pk] for pk in ids if pk in rows]}


def directory(tenant, params):
    qs = Student.objects.all()
    if params.get("id"):
        qs = qs.filter(pk=IntegerField(min_value=1, max_value=2**63 - 1).run_validation(params["id"]))
    if params.get("group"):
        group_id = IntegerField(min_value=1, max_value=2**63 - 1).run_validation(params["group"])
        qs = qs.filter(class_groups__tenant=tenant, class_groups__pk=group_id)
    page = student_page(qs, tenant, params)
    ids = [s["id"] for s in page["results"]]
    groups = defaultdict(list)
    for student, pk, name in ClassGroup.objects.filter(tenant=tenant, students__in=ids).order_by("name", "pk").values_list("students", "pk", "name"):
        groups[student].append({"id": pk, "name": name})
    for student in page["results"]:
        student["groups"] = groups[student["id"]]
    if params.get("id") and page["results"]:
        enrollments = Enrollment.objects.filter(tenant=tenant, student_id=ids[0], student__tenant=tenant, status="ACTIVE",
            lecture__tenant=tenant, lecture__class_group_link__group__tenant=tenant).order_by("lecture__title", "pk")
        page["results"][0]["courses"] = [{"id": course, "title": title, "group": group} for course, title, group in
            enrollments.values_list("lecture_id", "lecture__title", "lecture__class_group_link__group_id")]
    return page
