import re
import unicodedata

from collections import defaultdict

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, F, OuterRef, Q, Window
from django.db.models.functions import Coalesce, RowNumber
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from apps.domains.students import dawon_schema as schema
from drf_spectacular.utils import OpenApiParameter
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.support.lectures.class_group_cycles import assigned_class_group_ids, assign_cycle_status, class_group_cycles
from apps.core.models import TenantMembership
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.students.models import ClassGroup, ClassGroupLecture, Student
from apps.support.students.enrollment_matrix_dependencies import create_class_group_lecture, enroll_class_group_members, native_group_lecture


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").split())


def school_of(student):
    field = {"HIGH": "high_school", "MIDDLE": "middle_school", "ELEMENTARY": "elementary_school"}.get(student.school_type)
    return normalized(getattr(student, field, "") if field else "")


def with_student_access(queryset, tenant):
    return queryset.annotate(active_account=Exists(TenantMembership.objects.filter(
        tenant=tenant, user_id=OuterRef("user_id"), role="student", is_active=True, user__is_active=True)))


def eligible(group, student):
    return (student.deleted_at is None and student.is_managed and student.active_account
            and (group.kind == "union" or school_of(student) == group.school))


def assignable_instructors(tenant):
    from apps.domains.students.services.dawon_staff import sample_staff, TEACHER

    teaching = TenantMembership.objects.filter(
        tenant=tenant, user_id=OuterRef("user_id"), role="teacher", is_active=True,
    )
    return sample_staff(tenant).filter(Exists(teaching), position="INSTRUCTOR").exclude(
        Q(user__username=TEACHER) & Q(user__is_active=False)
    )


def effective_group_teacher(group):
    if group.assigned_teacher_id:
        return group.assigned_teacher if group.assigned_teacher.tenant_id == group.tenant_id else None
    links = group.course_links.filter(lecture__tenant_id=group.tenant_id, reference_teacher__tenant_id=group.tenant_id)
    from django.contrib.auth import get_user_model
    candidates = list(get_user_model().objects.filter(tenant_id=group.tenant_id,
        pk__in=links.values("reference_teacher_id")).only("pk","name").order_by("pk")[:2])
    if len(candidates) == 1:
        return candidates[0]
    if not candidates and group.created_by_id and TenantMembership.objects.filter(
        tenant_id=group.tenant_id,user_id=group.created_by_id,user__tenant_id=group.tenant_id,role="teacher",is_active=True
    ).exists():
        return group.created_by
    return None


class GroupInput(serializers.Serializer):
    assigned_teacher_id = serializers.IntegerField(min_value=1, max_value=2**63-1, required=False)
    name = serializers.CharField(max_length=100)
    kind = serializers.ChoiceField(choices=ClassGroup.Kind.choices)
    track = serializers.ChoiceField(choices=ClassGroup.Track.choices, allow_blank=True, default="")
    school = serializers.CharField(max_length=100, allow_blank=True, default="")

    def validate(self, attrs):
        attrs["name"] = normalized(attrs["name"])
        attrs["school"] = normalized(attrs["school"])
        if not attrs["name"] or max(len(attrs["name"]), len(attrs["school"])) > 100:
            raise serializers.ValidationError("반 이름과 학교 이름을 확인하세요.")
        if attrs["kind"] == "union" and (not attrs["track"] or attrs["school"]):
            raise serializers.ValidationError("연합반은 수준을 선택하고 학교는 비워 주세요.")
        if attrs["kind"] == "school" and (not attrs["school"] or attrs["track"]):
            raise serializers.ValidationError("학교별 내신반은 학교 이름을 입력하고 수준은 비워 주세요.")
        return attrs


class MemberInput(serializers.Serializer):
    add = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=500, default=list)
    remove = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=500, default=list)

    def validate(self, attrs):
        if set(attrs["add"]) & set(attrs["remove"]):
            raise serializers.ValidationError("같은 학생을 동시에 추가하고 제외할 수 없습니다.")
        return attrs


class LectureInput(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    period = serializers.ChoiceField(choices=["vacation", "midterm", "final"])
    school = serializers.CharField(max_length=100, allow_blank=True, default="")
    start_date = serializers.DateField()
    end_date = serializers.DateField()

    def validate(self, attrs):
        if attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError({"end_date": "종료일은 시작일 이후여야 합니다."})
        return attrs


def values(schema, request):
    serializer = schema(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def summary(group, count, lecture_count):
    return {"id": group.id, "name": group.name, "kind": group.kind, "track": group.track,
            "school": group.school, "student_count": count, "lecture_count": lecture_count}


def student_row(student):
    return {"id": student.id, "name": student.name, "school": school_of(student), "grade": student.grade,
            "phone": student.phone or "", "parent_phone": student.parent_phone or ""}


def lecture_row(link, count):
    lecture = link.lecture
    return {"id": lecture.pk, "title": lecture.title, "period": link.period, "school": link.school, "start_date": lecture.start_date,
            "end_date": lecture.end_date, "is_active": lecture.is_active, "student_count": count, "color": lecture.color,
            "chip_label": lecture.chip_label, "lecture_time": lecture.lecture_time}


class ClassGroupViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    lookup_value_regex = r"[1-9]\d*"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        tenant = request.tenant
        if not getattr(settings, "DAWON_REVIEW_CLASS_GROUPS", False) or (tenant.pk, tenant.code) != (719, "dawon"):
            raise NotFound()

        role = request.auth.get("dawon_demo_role") if hasattr(request.auth, "get") else None
        if role is None:
            memberships = set(TenantMembership.objects.filter(tenant=tenant,user=request.user,is_active=True).values_list("role",flat=True))
            if "teacher" in memberships and not memberships.intersection({"admin","owner"}):
                role = "teacher"
        self.teacher_scope = role == "teacher"
        if self.teacher_scope and self.action in {"create","partial_update","members","candidates"}:
            raise PermissionDenied("반 편성은 원장 업무입니다.")
        if role == "admin":
            user = request.user
            if (user.tenant_id != tenant.pk or user.username != "t719_dawon-demo-admin" or user.is_staff or user.is_superuser
                    or not TenantMembership.objects.filter(tenant=tenant,user=user,role="admin",is_active=True).exists()):
                raise PermissionDenied()

    def groups(self):
        request = self.request
        groups = ClassGroup.objects.filter(tenant=request.tenant)
        if self.teacher_scope:
            groups = groups.filter(pk__in=assigned_class_group_ids(request.tenant, request.user))
        return groups

    def group(self, pk, *, lock=False):
        qs = self.groups()
        return get_object_or_404(qs.select_for_update() if lock else qs, pk=pk)

    def group_detail(self, group):
        students = with_student_access(group.students.filter(tenant=self.request.tenant), self.request.tenant).order_by("name", "pk")
        roster = [dict(student_row(s), eligible=eligible(group, s)) for s in students]
        links = group.course_links.filter(lecture__tenant=self.request.tenant).select_related("lecture").annotate(
            active_count=Count("lecture__enrollments", filter=Q(lecture__enrollments__status="ACTIVE",
                lecture__enrollments__tenant=self.request.tenant, lecture__enrollments__student__tenant=self.request.tenant,
                lecture__enrollments__student__deleted_at__isnull=True))
        ).order_by(F("lecture__start_date").desc(nulls_last=True), "-pk")
        lectures = assign_cycle_status([lecture_row(link, link.active_count) for link in links], timezone.localdate())
        if hasattr(self.request.auth, "get") and self.request.auth.get("dawon_demo_role") == "admin":
            from apps.domains.enrollment.models import Enrollment

            by_student = defaultdict(list)
            enrolled = Enrollment.objects.filter(
                tenant=group.tenant, status="ACTIVE", student__tenant=group.tenant, student__deleted_at__isnull=True,
                student_id__in=[s["id"] for s in roster], lecture__tenant=group.tenant, lecture__is_active=True,
                lecture_id__in=[course["id"] for course in lectures],
            ).order_by("lecture__title", "lecture_id").values_list("student_id","lecture_id")
            for student_id, course_id in enrolled:
                by_student[student_id].append(course_id)
            for student in roster:
                student["course_ids"] = by_student[student["id"]]
        instructor = effective_group_teacher(group)
        result = {**summary(group, len(roster), len(lectures)), "students": roster, "lectures": lectures,
                  "assigned_teacher": {"id":instructor.pk,"name":instructor.name} if instructor else None}
        return result

    @extend_schema(operation_id="students_class_groups_list", responses={200: schema.variants(schema.array(schema.group), schema.array(schema.person))},
                   parameters=[OpenApiParameter("view", str, enum=["instructors"])])
    def list(self, request):
        if request.query_params.get("view") == "instructors":
            if not hasattr(request.auth, "get") or request.auth.get("dawon_demo_role") != "admin":
                raise PermissionDenied()
            return Response([{"id":user_id,"name":name} for user_id, name in
                assignable_instructors(request.tenant).order_by("name","pk").values_list("user_id","name")],
                headers={"Cache-Control":"private, no-store"})
        groups = self.groups().annotate(
            count=Count("students", filter=Q(students__tenant=request.tenant), distinct=True),
            course_count=Count("course_links", filter=Q(course_links__lecture__tenant=request.tenant), distinct=True),
        ).order_by("name", "pk")
        groups = list(groups.select_related("assigned_teacher"))
        legacy = defaultdict(set)
        for group_id, name in ClassGroupLecture.objects.filter(
            group_id__in=[g.pk for g in groups if not g.assigned_teacher_id],
            group__tenant=request.tenant, lecture__tenant=request.tenant, reference_teacher__tenant=request.tenant
        ).values_list("group_id","reference_teacher__name"):
            legacy[group_id].add(name)
        return Response([{**summary(g,g.count,g.course_count),
            "instructor_name": g.assigned_teacher.name if g.assigned_teacher_id and g.assigned_teacher.tenant_id==request.tenant.pk
            else ", ".join(sorted(legacy[g.pk]))} for g in groups])

    @extend_schema(responses={200: schema.teaching})
    @action(detail=False, methods=["get"])
    def teaching(self, request):
        from apps.domains.lectures.models import Session

        tenant = request.tenant
        assigned = assigned_class_group_ids(tenant, request.user)
        groups = ClassGroup.objects.filter(pk__in=assigned, tenant=tenant).annotate(
            members=Count("students", filter=Q(students__tenant=tenant, students__deleted_at__isnull=True)),
        ).order_by("name", "pk")
        rows = {g.pk: {"id": g.pk, "name": g.name, "kind": g.kind, "track": g.track, "school": g.school, "members": g.members, "courses": [], "recent": None} for g in groups}
        links = ClassGroupLecture.objects.filter(group_id__in=rows, lecture__tenant=tenant)
        for group_id, lecture_id in links.values_list("group_id", "lecture_id"):
            rows[group_id]["courses"].append(lecture_id)
        recent = Session.objects.filter(lecture__tenant=tenant, lecture__class_group_link__group_id__in=rows).annotate(
            group_id=F("lecture__class_group_link__group_id"), schedule_id=F("schedules__pk"),
            held_date=Coalesce("schedules__date", "date"),
        ).filter(held_date__lte=timezone.localdate()).annotate(
            position=Window(RowNumber(), partition_by=F("group_id"), order_by=[
                F("held_date").desc(), F("schedules__start_time").desc(nulls_last=True), F("pk").desc(),
                F("schedule_id").desc(nulls_last=True),
            ]),
        ).filter(position=1).order_by().values("group_id", "id", "lecture_id", "title", "held_date", "schedule_id")
        for lesson in recent:
            group_id = lesson.pop("group_id")
            lesson["date"] = lesson.pop("held_date")
            rows[group_id]["recent"] = lesson
        return Response(list(rows.values()))

    @extend_schema(request=GroupInput, responses={201: schema.group_detail})
    def create(self, request):
        fields = values(GroupInput, request)
        teacher_id = fields.get("assigned_teacher_id")
        try:
            with transaction.atomic():
                if teacher_id is not None:
                    if not hasattr(request.auth, "get") or request.auth.get("dawon_demo_role") != "admin":
                        raise PermissionDenied()
                    if not assignable_instructors(request.tenant).select_for_update().filter(user_id=teacher_id).first():
                        raise serializers.ValidationError({"assigned_teacher_id":"이 지점의 재직 강사를 선택해 주세요."})
                group = ClassGroup.objects.create(tenant=request.tenant, created_by=request.user, **fields)
        except IntegrityError:
            raise serializers.ValidationError({"name": "같은 이름의 반이 이미 있습니다."})
        return Response(self.group_detail(group), status=201)

    @extend_schema(request={"application/json": schema.record(assigned_teacher_id=schema.integer)}, responses={200: schema.group_detail})
    @transaction.atomic
    def partial_update(self, request, pk):
        if set(request.data) != {"assigned_teacher_id"}:
            raise serializers.ValidationError("담당 강사를 선택하세요.")
        roles = TenantMembership.objects.filter(tenant=request.tenant,user=request.user,is_active=True)
        if not roles.filter(role__in=("admin","owner")).exists():
            raise PermissionDenied()
        group = self.group(pk,lock=True)
        teacher_id = serializers.IntegerField(min_value=1,max_value=2**63-1).run_validation(request.data["assigned_teacher_id"])
        staff = get_object_or_404(assignable_instructors(request.tenant).select_related("user").select_for_update(),user_id=teacher_id)
        links = list(group.course_links.select_related("lecture").select_for_update().filter(lecture__tenant=request.tenant)
                     .order_by(F("lecture__start_date").desc(nulls_last=True),"-pk"))
        cycles = assign_cycle_status([lecture_row(link,0) for link in links],timezone.localdate())
        targets = [link for link,cycle in zip(links,cycles) if cycle["cycle_status"] != "past"]
        group.assigned_teacher = staff.user
        group.save(update_fields=["assigned_teacher"])
        if targets:
            ClassGroupLecture.objects.filter(pk__in=[link.pk for link in targets]).update(reference_teacher_id=teacher_id)
            from apps.domains.lectures.models import Lecture
            Lecture.objects.filter(tenant=request.tenant,pk__in=[link.lecture_id for link in targets]).update(name=staff.name)
        return Response(self.group_detail(group))

    @extend_schema(responses={200: schema.group_detail})
    def retrieve(self, request, pk):
        return Response(self.group_detail(self.group(pk)))

    def candidate_rows(self, group, qs, request, *, school=""):
        params = request.query_params
        offset_field = serializers.IntegerField(min_value=0)
        offset = offset_field.run_validation(params.get("offset", 0))
        search = normalized(params.get("search", "")).casefold()
        exact = params.get("exact", "").lower() == "true"
        phones = {field: re.sub(r"\D", "", params[field]) for field in ("phone", "parent_phone") if params.get(field)}
        rows = []
        qs = with_student_access(qs.filter(tenant=request.tenant, deleted_at__isnull=True, is_managed=True),
                                 request.tenant).filter(active_account=True)
        for student in qs.only("pk", "name", "school_type", "high_school", "middle_school", "elementary_school",
                               "deleted_at", "is_managed", "phone", "parent_phone", "grade").order_by("name", "pk").iterator(chunk_size=200):
            if not eligible(group, student) or (school and school_of(student) != school):
                continue
            name = normalized(student.name).casefold()
            if exact and name != search:
                continue
            if not exact and search and search not in name and search not in school_of(student).casefold():
                continue
            if any(not digits or re.sub(r"\D", "", getattr(student, field) or "") != digits for field, digits in phones.items()):
                continue
            if offset:
                offset -= 1
                continue
            rows.append(student_row(student))
            if len(rows) == 50:
                break
        return rows

    @extend_schema(responses={200: schema.array(schema.student)})
    @action(detail=True, methods=["get"])
    def candidates(self, request, pk):
        group = self.group(pk)
        return Response(self.candidate_rows(group, Student.objects.exclude(class_groups=group), request))

    @extend_schema(methods=["GET"], responses={200: schema.reference})
    @extend_schema(methods=["PATCH"], request={"application/json": schema.record(top_reference_percent=schema.integer)}, responses={200: schema.reference})
    @action(detail=False, methods=["get", "patch"], url_path=r"for-lecture/(?P<lecture_id>[1-9]\d*)")
    def for_lecture(self, request, lecture_id):
        lecture = native_group_lecture(request.tenant, lecture_id)
        link = ClassGroupLecture.objects.filter(group__tenant=request.tenant, lecture=lecture).select_related("group").first()
        if link and not self.groups().filter(pk=link.group_id).exists():
            raise NotFound()
        roles = TenantMembership.objects.filter(tenant=request.tenant, user=request.user, is_active=True)
        can_edit = bool(link and (roles.filter(role__in=("owner", "admin")).exists()
                        or (link.reference_teacher_id == request.user.pk and roles.filter(role="teacher").exists())))
        if request.method == "PATCH":
            if not can_edit:
                raise PermissionDenied("담당 강사 또는 원장·관리자만 비교 기준을 변경할 수 있습니다.")
            percent = serializers.IntegerField(min_value=1, max_value=100).run_validation(request.data.get("top_reference_percent"))
            link.top_reference_percent = percent
            link.save(update_fields=["top_reference_percent"])
        return Response({"group_id": link.group_id if link else None, "name": link.group.name if link else "",
                         "school": link.school if link else "", "can_edit_top_reference": can_edit,
                         "top_reference_percent": link.top_reference_percent if link else None})

    @extend_schema(methods=["GET"], responses={200: schema.array(schema.student)})
    @extend_schema(methods=["POST"], request={"application/json": schema.record(student_ids=schema.array(schema.integer))},
                   responses={201: schema.record(created_count=schema.integer, student_ids=schema.array(schema.integer))})
    @action(detail=True, methods=["get", "post"], url_path=r"lectures/(?P<lecture_pk>[1-9]\d*)/late-students")
    @transaction.atomic
    def late_students(self, request, pk, lecture_pk):
        write = request.method == "POST"
        group = self.group(pk, lock=write)
        link = get_object_or_404(group.course_links.select_related("lecture"), lecture_id=lecture_pk, lecture__tenant=request.tenant)
        existing = link.lecture.enrollments.filter(tenant=request.tenant)
        members = group.students.filter(tenant=request.tenant)
        if not write:
            return Response(self.candidate_rows(group, members.exclude(pk__in=existing.values("student_id")), request, school=link.school))
        field = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False, max_length=500)
        ids = sorted(set(field.run_validation(request.data.get("student_ids"))))
        selected = list(with_student_access(members.select_for_update().filter(pk__in=ids), request.tenant).order_by("pk"))
        if (len(selected) != len(ids) or any(not eligible(group, s) or (link.school and school_of(s) != link.school)
                                             for s in selected) or existing.filter(student_id__in=ids).exists()):
            raise serializers.ValidationError("소속 반, 학교, 재원 상태 또는 기존 수강 이력을 확인하세요.")
        enroll_class_group_members(tenant=request.tenant, lecture_id=link.lecture_id, student_ids=ids)
        return Response({"created_count": len(ids), "student_ids": ids}, status=201)

    @extend_schema(request=MemberInput, responses={200: schema.group_detail})
    @action(detail=True, methods=["patch"])
    @transaction.atomic
    def members(self, request, pk):
        group = self.group(pk, lock=True)
        fields = values(MemberInput, request)
        add, remove = set(fields["add"]), set(fields["remove"])
        ids = add | remove
        students = {s.pk: s for s in with_student_access(Student.objects.select_for_update().filter(
            tenant=request.tenant, pk__in=ids), request.tenant).order_by("pk")}
        if set(students) != ids or any(not eligible(group, students[pk]) for pk in add):
            raise serializers.ValidationError("재원 상태, 소속 학원과 학교를 확인하세요.")
        group.students.remove(*remove)
        group.students.add(*add)
        return Response(self.group_detail(group))

    @extend_schema(request=LectureInput, responses={201: schema.course})
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def lectures(self, request, pk):
        group = self.group(pk, lock=True)
        fields = values(LectureInput, request)
        roster = list(with_student_access(group.students.select_for_update().filter(tenant=request.tenant), request.tenant).order_by("pk"))
        school = normalized(fields["school"])
        if group.kind == "union" and fields["period"] in ("midterm", "final"):
            roster = [student for student in roster if school and school_of(student) == school]
            if not roster or not any(eligible(group, student) for student in roster):
                raise serializers.ValidationError({"school": "이 반에 재원생이 있는 학교를 선택하세요."})
        elif group.kind == "school":
            if school and school != group.school:
                raise serializers.ValidationError({"school": "학교별 내신반의 학교와 일치해야 합니다."})
            school = group.school
        elif school:
            raise serializers.ValidationError({"school": "방학 연합반은 학교를 구분하지 않습니다."})
        if any(not eligible(group, student) for student in roster):
            raise serializers.ValidationError("학교 또는 재원 상태가 바뀐 학생을 명부에서 제외한 뒤 다시 시도하세요.")
        if group.course_links.filter(lecture__tenant=request.tenant, period=fields["period"], school=school,
                                     lecture__start_date=fields["start_date"]).exists():
            raise serializers.ValidationError({"start_date": "같은 학교·기간·시작일의 강의가 이미 있습니다."})
        instructor = effective_group_teacher(group)
        demo_role = request.auth.get("dawon_demo_role") if hasattr(request.auth,"get") else None
        if demo_role in ("admin","teacher") and not instructor:
            raise serializers.ValidationError("수업을 개설하기 전에 반 담당 강사를 지정하세요.")
        if demo_role == "admin" and not assignable_instructors(request.tenant).filter(user_id=instructor.pk).exists():
            raise serializers.ValidationError("수업을 개설하기 전에 반 담당 강사를 지정하세요.")
        try:
            with transaction.atomic():
                lecture = create_class_group_lecture(tenant=request.tenant, title=fields["title"],
                    start_date=fields["start_date"], end_date=fields["end_date"], student_ids=[s.pk for s in roster],
                    options={**{key: request.data[key] for key in ("name", "subject", "description", "lecture_time", "color",
                             "chip_label", "schedule_mode", "schedule_defaults") if key in request.data}, **({"name":instructor.name} if instructor else {})})
        except IntegrityError:
            raise serializers.ValidationError({"title": "같은 이름의 강의가 있는지 확인하세요."})
        teachers = TenantMembership.objects.filter(tenant=request.tenant, role="teacher", is_active=True, user__is_active=True)
        named = list(teachers.filter(user__name=lecture.name).values_list("user_id", flat=True)[:2])
        teacher_id = named[0] if len(named) == 1 else None
        if not named and teachers.filter(user=request.user).exists():
            teacher_id = request.user.pk
        link = ClassGroupLecture.objects.create(group=group, lecture=lecture, period=fields["period"], school=school,
                                               reference_teacher_id=instructor.pk if instructor else teacher_id)
        cycle = class_group_cycles(request.tenant, group_id=group.pk)[lecture.pk]
        return Response({**lecture_row(link, len(roster)), **cycle}, status=201)
