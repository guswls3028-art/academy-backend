import re
import unicodedata

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

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


class GroupInput(serializers.Serializer):
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
    return {"id": student.id, "name": student.name, "school": school_of(student),
            "phone": student.phone or "", "parent_phone": student.parent_phone or ""}


def lecture_row(link, count):
    lecture = link.lecture
    return {"id": lecture.pk, "title": lecture.title, "period": link.period, "school": link.school, "start_date": lecture.start_date,
            "end_date": lecture.end_date, "student_count": count}


class ClassGroupViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    lookup_value_regex = r"[1-9]\d*"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        tenant = request.tenant
        if not getattr(settings, "DAWON_REVIEW_CLASS_GROUPS", False) or (tenant.pk, tenant.code) != (719, "dawon"):
            raise NotFound()

    def group(self, pk, *, lock=False):
        qs = ClassGroup.objects.filter(tenant=self.request.tenant)
        return get_object_or_404(qs.select_for_update() if lock else qs, pk=pk)

    def group_detail(self, group):
        students = with_student_access(group.students.filter(tenant=self.request.tenant), self.request.tenant).order_by("name", "pk")
        roster = [dict(student_row(s), eligible=eligible(group, s)) for s in students]
        links = group.course_links.filter(lecture__tenant=self.request.tenant).select_related("lecture").annotate(
            active_count=Count("lecture__enrollments", filter=Q(lecture__enrollments__status="ACTIVE",
                lecture__enrollments__tenant=self.request.tenant, lecture__enrollments__student__tenant=self.request.tenant,
                lecture__enrollments__student__deleted_at__isnull=True))
        ).order_by("-lecture__start_date", "-pk")
        lectures = [lecture_row(link, link.active_count) for link in links]
        return {**summary(group, len(roster), len(lectures)), "students": roster, "lectures": lectures}

    def list(self, request):
        groups = ClassGroup.objects.filter(tenant=request.tenant).annotate(
            count=Count("students", filter=Q(students__tenant=request.tenant), distinct=True),
            course_count=Count("course_links", filter=Q(course_links__lecture__tenant=request.tenant), distinct=True),
        ).order_by("name", "pk")
        return Response([summary(g, g.count, g.course_count) for g in groups])

    def create(self, request):
        fields = values(GroupInput, request)
        try:
            with transaction.atomic():
                group = ClassGroup.objects.create(tenant=request.tenant, **fields)
        except IntegrityError:
            raise serializers.ValidationError({"name": "같은 이름의 반이 이미 있습니다."})
        return Response(self.group_detail(group), status=201)

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
                               "deleted_at", "is_managed", "phone", "parent_phone").order_by("name", "pk").iterator(chunk_size=200):
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

    @action(detail=True, methods=["get"])
    def candidates(self, request, pk):
        group = self.group(pk)
        return Response(self.candidate_rows(group, Student.objects.exclude(class_groups=group), request))

    @action(detail=False, methods=["get"], url_path=r"for-lecture/(?P<lecture_id>[1-9]\d*)")
    def for_lecture(self, request, lecture_id):
        lecture = native_group_lecture(request.tenant, lecture_id)
        link = ClassGroupLecture.objects.filter(group__tenant=request.tenant, lecture=lecture).select_related("group").first()
        return Response({"group_id": link.group_id if link else None, "name": link.group.name if link else "",
                         "school": link.school if link else ""})

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
        try:
            with transaction.atomic():
                lecture = create_class_group_lecture(tenant=request.tenant, title=fields["title"],
                    start_date=fields["start_date"], end_date=fields["end_date"], student_ids=[s.pk for s in roster],
                    options={key: request.data[key] for key in ("name", "subject", "description", "lecture_time", "color",
                             "chip_label", "schedule_mode", "schedule_defaults") if key in request.data})
        except IntegrityError:
            raise serializers.ValidationError({"title": "같은 이름의 강의가 있는지 확인하세요."})
        link = ClassGroupLecture.objects.create(group=group, lecture=lecture, period=fields["period"], school=school)
        return Response(lecture_row(link, len(roster)), status=201)
