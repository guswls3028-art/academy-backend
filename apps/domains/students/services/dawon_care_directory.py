"""Director read models for the dedicated demo. Keep the original clinic, counsel and enrollment records."""
from collections import defaultdict
from django.db.models import Exists, F, OuterRef, Q
from rest_framework.exceptions import NotFound
from rest_framework.fields import ChoiceField, IntegerField

from apps.domains.clinic.models import SessionParticipant
from apps.domains.community.models import PostEntity, PostReply
from apps.domains.enrollment.models import Enrollment
from apps.domains.students.models import ClassGroup, DawonCounselAppointment


def selected_group(tenant, query):
    if not query.get("group"):
        return None
    group_id = IntegerField(min_value=1, max_value=2**63 - 1).run_validation(query.get("group"))
    try:
        return ClassGroup.objects.get(tenant=tenant, pk=group_id)
    except ClassGroup.DoesNotExist:
        raise NotFound()


def page_of(rows, query):
    page = IntegerField(min_value=1, max_value=1000000).run_validation(query.get("page", 1))
    start = (page - 1) * 25
    return rows.count(), rows[start:start + 25]


def learner_directory(tenant, query):
    group = selected_group(tenant, query)
    rows = Enrollment.objects.filter(
        tenant=tenant, student__tenant=tenant, status="ACTIVE", student__deleted_at__isnull=True,
        lecture__tenant=tenant, lecture__class_group_link__group__tenant=tenant,
    )
    if group:
        rows = rows.filter(lecture__class_group_link__group=group)
    if term := query.get("q", "").strip()[:100]:
        rows = rows.filter(student__name__icontains=term)
    count, page = page_of(rows.order_by("student__name","student_id","lecture__title","pk"), query)
    return {"count":count,"rows":list(page.values(
        "student_id","lecture_id", name=F("student__name"), course=F("lecture__title"),
        group_id=F("lecture__class_group_link__group_id"), group=F("lecture__class_group_link__group__name"),
    ))}


def care_directory(tenant, query):
    kind = ChoiceField(choices=("clinic","requests","appointments")).run_validation(query.get("kind"))
    group = selected_group(tenant, query)
    term = query.get("q","").strip()[:100]
    if kind == "clinic":
        source = SessionParticipant.objects.filter(tenant=tenant, student__tenant=tenant, student__deleted_at__isnull=True).filter(
            Q(session__isnull=True) | Q(session__tenant=tenant),
        ).filter(Q(enrollment__isnull=True) | Q(enrollment__tenant=tenant, enrollment__lecture__tenant=tenant, enrollment__student_id=F("student_id")))
        if group:
            source = source.filter(Q(enrollment__lecture__class_group_link__group=group)
                                   | Q(enrollment__isnull=True, student__class_groups=group))
        if term:
            source = source.filter(student__name__icontains=term)
        source = source.select_related("student","session","enrollment__lecture__class_group_link__group").order_by("-created_at","-pk")
    elif kind == "requests":
        replies = PostReply.objects.filter(tenant=tenant, post_id=OuterRef("pk"),author_role="staff")
        source = PostEntity.objects.filter(tenant=tenant,post_type="counsel",author_role="parent",status="published",
                                           created_by__tenant=tenant,created_by__deleted_at__isnull=True).annotate(answered=Exists(replies))
        if group:
            source = source.filter(created_by__class_groups=group)
        if term:
            source = source.filter(created_by__name__icontains=term)
        source = source.select_related("created_by").order_by("-created_at","-pk")
    else:
        source = DawonCounselAppointment.objects.filter(enrollment__tenant=tenant,enrollment__student__tenant=tenant,
                  enrollment__student__deleted_at__isnull=True,enrollment__lecture__tenant=tenant)
        if group:
            source = source.filter(enrollment__lecture__class_group_link__group=group)
        if term:
            source = source.filter(enrollment__student__name__icontains=term)
        source = source.select_related("enrollment__student","enrollment__lecture__class_group_link__group").order_by("-day","-pk")
    detail = "record" in query
    if detail:
        source = source.filter(pk=IntegerField(min_value=1,max_value=2**63-1).run_validation(query["record"]))
    count, page = page_of(source, query)
    if detail and not count:
        raise NotFound()
    rows = []
    for item in page:
        enrollment = getattr(item,"enrollment",None)
        link = getattr(enrollment.lecture,"class_group_link",None) if enrollment else None
        home = link.group.name if link and link.group.tenant_id == tenant.pk else ""
        if kind == "requests":
            student = item.created_by
            fields = {"day":item.created_at.date(),"title":item.title,"status":"답변 완료" if item.answered else "답변 대기"}
            if detail:
                fields.update(body=item.content,replies=list(item.replies.filter(tenant=tenant).order_by("created_at","pk").values_list("content",flat=True)))
        elif kind == "appointments":
            student = enrollment.student
            fields = {"day":item.day,"title":enrollment.lecture.title,"status":"상담 예정"}
        else:
            student = item.student
            fields = {"day":item.session.date if item.session else item.requested_date,
                      "title":item.session.title if item.session else "클리닉 신청",
                      "status":item.status}
            if detail:
                fields["body"] = item.student_request_memo
        rows.append({"id":item.pk,"student_id":student.pk,"name":student.name,"groups":[home] if home else [],
                     "course_id":enrollment.lecture_id if enrollment else None,**fields})
    missing = {row["student_id"] for row in rows if not row["groups"]}
    groups = defaultdict(list)
    for student_id, name in ClassGroup.objects.filter(tenant=tenant,students__in=missing).order_by("name").values_list("students","name"):
        groups[student_id].append(name)
    for row in rows:
        if not row["groups"]:
            row["groups"] = groups[row["student_id"]]
    return {"count":count,"rows":rows}
