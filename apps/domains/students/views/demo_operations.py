"""Operations on the designated public review fixtures in the isolated Dawon tenant."""
from collections import defaultdict
from statistics import fmean

from django.db.models import Count, Exists, F, Max, Min, OuterRef, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.fields import IntegerField
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import TenantMembership
from apps.domains.clinic.contracts import is_demo_tenant
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models.homework import Homework
from apps.domains.homework_results.models.score import HomeworkScore
from apps.domains.submissions.models import Submission
from apps.domains.lectures.models import Lecture, Session
from apps.domains.staffs.models import Staff
from apps.domains.exams.models import Exam
from apps.domains.results.models import Result
from apps.domains.results.contracts import load_initial_exam_scores, project_initial_exam_score
from apps.domains.results.services.omr_subjective_completion import pending_omr_result_ids
from apps.domains.students import dawon_schema as schema
from apps.domains.students.models import ClassGroup, DawonExamReview
from apps.domains.students.services.dawon_daily_attendance import daily_attendance
from apps.domains.students.services.dawon_exam_reports import admin_exam_detail, exam_for_management, student_results
from apps.domains.students.services.dawon_care_directory import care_directory, learner_directory
from apps.domains.students.services.dawon_followup import class_alerts, register_followup, student_record
from apps.domains.teachers.contracts import active_teachers


def homework_progress(tenant, courses):
    submissions = Submission.objects.filter(
        tenant=tenant, target_type=Submission.TargetType.HOMEWORK,
        target_id=OuterRef("homework_id"), enrollment_id=OuterRef("enrollment_id"),
    ).exclude(status=Submission.Status.SUPERSEDED)
    scores = HomeworkScore.objects.filter(
        homework_id=OuterRef("homework_id"), session_id=OuterRef("session_id"),
        enrollment_id=OuterRef("enrollment_id"), attempt_index=1,
    )
    rows = HomeworkAssignment.objects.filter(
        tenant=tenant, homework__tenant=tenant, session_id=F("homework__session_id"),
        session__lecture_id__in=courses, enrollment__lecture_id=F("session__lecture_id"),
        enrollment__tenant=tenant, enrollment__status="ACTIVE", enrollment__student__tenant=tenant,
        enrollment__student__deleted_at__isnull=True,
    ).annotate(
        uploaded=Exists(submissions),
        absent=Exists(scores.filter(meta__status=HomeworkScore.MetaStatus.NOT_SUBMITTED)),
        deferred=Exists(scores.filter(meta__status=HomeworkScore.MetaStatus.VIDEO_MAKEUP)),
        marked=Exists(scores.filter(Q(score__isnull=False) | Q(teacher_approved=True))),
    ).values_list("homework_id", "uploaded", "absent", "deferred", "marked")
    counts = defaultdict(lambda: {"assigned": 0, "submitted": 0, "missing": 0, "graded": 0, "deferred": 0})
    for homework_id, uploaded, absent, deferred, marked in rows:
        progress = counts[homework_id]
        progress["assigned"] += 1
        if deferred:
            progress["deferred"] += 1
        elif absent or not (uploaded or marked):
            progress["missing"] += 1
        else:
            progress["submitted"] += 1
            progress["graded"] += int(marked)
    return counts


class DawonDemoOperationsView(APIView):
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        tenant = getattr(request, "tenant", None)
        token = request.auth
        if not (
            is_demo_tenant(tenant) and hasattr(token, "get") and token.get("dawon_demo_role") == "admin"
            and request.user.username == "t719_dawon-demo-admin" and request.user.tenant_id == tenant.pk
            and not request.user.is_staff and not request.user.is_superuser
            and TenantMembership.objects.filter(tenant=tenant, user=request.user, role="admin", is_active=True).exists()
        ):
            raise NotFound()

    def employee(self, tenant):
        return Staff.objects.filter(
            tenant=tenant, name__in=("다원 체험 강사", "김선생 2"), phone="", is_manager=False,
            user__tenant=tenant, user__username="t719_dawon-demo-employee",
            user__is_staff=False, user__is_superuser=False, is_active=True,
        )

    @extend_schema(responses={200: schema.operations}, parameters=[
        OpenApiParameter("view", str, enum=["attendance", "care", "learners", "student_results"]),
        OpenApiParameter("q", str), OpenApiParameter("page", int), OpenApiParameter("lecture", int),
        OpenApiParameter("status", str, enum=["graded", "absent", "pending", "no_exam"]),
        OpenApiParameter("sort", str, enum=["name", "recent", "decline", "improvement", "lowest"]),
        OpenApiParameter("trend", str, enum=["decline", "improvement"]), OpenApiParameter("export", int, enum=[1]),
        OpenApiParameter("date", OpenApiTypes.DATE), OpenApiParameter("group", int),
        OpenApiParameter("course_overview", int), OpenApiParameter("student", int),
        OpenApiParameter("course", int), OpenApiParameter("exam", int), OpenApiParameter("alerts", str),
    ])
    def get(self, request):
        tenant = request.tenant
        if request.query_params.get("view") == "student_results":
            return Response(student_results(tenant, request.query_params), headers={"Cache-Control":"private, no-store"})
        if request.query_params.get("view") == "attendance":
            return Response(daily_attendance(tenant, request.query_params), headers={"Cache-Control": "private, no-store"})
        if request.query_params.get("view") in {"care", "learners"}:
            select = care_directory if request.query_params["view"] == "care" else learner_directory
            return Response(select(tenant, request.query_params), headers={"Cache-Control":"private, no-store"})
        if "alerts" in request.query_params:
            return Response(class_alerts(tenant, request.query_params.get("group")), headers={"Cache-Control":"private, no-store"})
        if "student" in request.query_params:
            return Response(student_record(tenant, request.query_params), headers={"Cache-Control":"private, no-store"})
        if "exam" in request.query_params:
            exam, course_id = self.selected_exam(tenant, request.query_params)
            response = Response(admin_exam_detail(tenant, exam, course_id, request.user))
            response["Cache-Control"] = "no-store"
            return response
        groups = {
            group.pk: {"id": group.pk, "name": group.name, "members": group.member_count, "courses": []}
            for group in ClassGroup.objects.filter(tenant=tenant).annotate(
                member_count=Count("students", filter=Q(students__tenant=tenant, students__deleted_at__isnull=True)),
            ).order_by("name", "pk")
        }
        instructor_names = {
            name.strip() for name in active_teachers(tenant).values_list("name", flat=True) if name
        }
        instructor_names.update(name.strip() for name in TenantMembership.objects.filter(
            tenant=tenant, user__tenant=tenant, role__in=["owner", "teacher"], is_active=True,
        ).values_list("user__name", flat=True) if name)
        courses = Lecture.objects.filter(tenant=tenant, is_system=False)
        focused = "course_overview" in request.query_params
        if focused:
            course_id = IntegerField(min_value=1, max_value=2**63 - 1).run_validation(request.query_params["course_overview"])
            courses = courses.filter(pk=course_id)
        courses = courses.annotate(
            students=Count("enrollments", filter=Q(
                enrollments__status="ACTIVE", enrollments__tenant=tenant,
                enrollments__student__tenant=tenant, enrollments__student__deleted_at__isnull=True,
            ), distinct=True),
            lessons=Count("sessions", distinct=True),
        ).values("id", "title", "is_active", "students", "lessons", "name", "color", "chip_label", "class_group_link__group_id",
                 "class_group_link__reference_teacher__tenant_id", "class_group_link__reference_teacher__name").order_by("title", "pk")
        course_details = {}
        for course in courses:
            legacy = course.pop("name").strip()
            reference = (course.pop("class_group_link__reference_teacher__name") or "").strip()
            same_tenant = course.pop("class_group_link__reference_teacher__tenant_id") == tenant.pk
            instructor = reference if same_tenant and course["class_group_link__group_id"] in groups else ""
            if not instructor and legacy != course["title"].strip() and legacy in instructor_names:
                instructor = legacy
            course.update(instructor=instructor, sessions=[], exams=[], homework=[])
            course_details[course["id"]] = course
            group_id = course.pop("class_group_link__group_id")
            if group_id not in groups:
                group_id = None
                groups.setdefault(None, {"id": None, "name": "미편성 강의", "members": None, "courses": []})
            groups[group_id]["courses"].append(course)

        for lesson in Session.objects.filter(lecture_id__in=course_details).order_by("order", "pk").values(
            "lecture_id", "id", "order", "title", "date",
        ):
            course_details[lesson.pop("lecture_id")]["sessions"].append(lesson)
        progress = homework_progress(tenant, course_details)
        for homework in Homework.objects.filter(
            tenant=tenant, homework_type=Homework.HomeworkType.REGULAR, session__lecture_id__in=course_details,
        ).order_by("session__order", "pk").values("id", "title", "status", "session_id", "session__lecture_id"):
            homework["progress"] = progress[homework["id"]]
            course_details[homework.pop("session__lecture_id")]["homework"].append(homework)

        exams = Exam.objects.filter(
            tenant=tenant, exam_type=Exam.ExamType.REGULAR, is_active=True,
            sessions__lecture__tenant=tenant,
        ).distinct().order_by("title", "pk")
        if focused:
            exams = exams.filter(sessions__lecture_id=course_id)
        linked_exam = Exam.objects.filter(
            tenant=tenant, pk=OuterRef("target_id"), sessions__lecture_id=OuterRef("enrollment__lecture_id"),
        )
        snapshots = Result.objects.filter(
            target_type="exam", target_id__in=exams.values("pk"), enrollment__tenant=tenant,
            enrollment__lecture__tenant=tenant, enrollment__student__tenant=tenant,
        ).filter(Exists(linked_exam))
        results = list(snapshots.select_related("enrollment"))
        pending = pending_omr_result_ids(results)
        initial = load_initial_exam_scores(
            exam_ids={row.target_id for row in results}, enrollment_ids={row.enrollment_id for row in results},
        )
        by_exam = defaultdict(list)
        by_course_exam = defaultdict(list)
        for row in results:
            if row.pk in pending:
                continue
            score = project_initial_exam_score(
                state=initial.get((row.target_id, row.enrollment_id)),
                fallback_score=row.total_score, fallback_max_score=row.max_score,
            )
            if not score.not_submitted and score.total_score is not None:
                by_exam[row.target_id].append(score.total_score)
                by_course_exam[row.enrollment.lecture_id, row.target_id].append(score.total_score)
        exam_courses = Exam.objects.filter(
            id__in=exams.values("id"), sessions__lecture_id__in=course_details,
        ).values_list("sessions__lecture_id", "id", "title").annotate(first_day=Min("sessions__date"), last_day=Max("sessions__date")).order_by("title", "id")
        for course_id, exam_id, title, first_day, last_day in exam_courses:
            scores = by_course_exam[course_id, exam_id]
            course_details[course_id]["exams"].append({
                "id": exam_id, "title": title, "date": first_day if first_day == last_day else None, "graded": len(scores), "average": fmean(scores) if scores else None,
            })
        response = Response({
            "groups": list(groups.values()), "employee": self.employee(tenant).values("name", "position").first(),
            "exams": [{
                "id": exam.pk, "title": exam.title, "graded": len(by_exam[exam.pk]),
                "average": fmean(by_exam[exam.pk]) if by_exam[exam.pk] else None,
            } for exam in exams.only("pk", "title")],
        })
        response["Cache-Control"] = "no-store"
        return response

    @staticmethod
    def selected_exam(tenant, data):
        try:
            exam_id, course_id = int(data["exam"]), int(data["course"])
        except (KeyError, ValueError, TypeError):
            raise ValidationError("반별 강의와 시험을 선택해 주세요.")
        return exam_for_management(tenant, exam_id, course_id), course_id

    @extend_schema(request={"application/json": schema.operation_patch}, responses={200: schema.operation_result})
    def patch(self, request):
        if isinstance(request.data, dict) and "followup" in request.data:
            return Response(register_followup(request), headers={"Cache-Control":"private, no-store"})
        if isinstance(request.data, dict) and set(request.data) == {"exam", "course", "reviewed"}:
            if type(request.data["reviewed"]) is not bool:
                raise ValidationError("검토 상태를 확인해 주세요.")
            exam, course_id = self.selected_exam(request.tenant, request.data)
            key = {"exam": exam, "lecture_id": course_id, "reviewer": request.user}
            if request.data["reviewed"]:
                DawonExamReview.objects.get_or_create(**key)
            else:
                DawonExamReview.objects.filter(**key).delete()
            return Response({"reviewed": request.data["reviewed"]})
        if not isinstance(request.data, dict) or set(request.data) != {"position"} or request.data["position"] not in ("INSTRUCTOR", "ASSISTANT"):
            raise ValidationError("강사 또는 조교 직위를 선택해 주세요.")
        if not self.employee(request.tenant).update(position=request.data["position"]):
            raise NotFound()
        return Response({"position": request.data["position"]})
