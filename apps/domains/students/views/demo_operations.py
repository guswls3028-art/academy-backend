"""Operations on the designated public review fixtures in the isolated Dawon tenant."""
from collections import defaultdict
from statistics import fmean

from django.db.models import Count, Exists, OuterRef, Q
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import TenantMembership
from apps.domains.clinic.dawon_demo import is_demo_tenant
from apps.domains.lectures.models import Lecture
from apps.domains.staffs.models import Staff
from apps.domains.exams.models import Exam
from apps.domains.results.models import Result
from apps.domains.results.utils.initial_exam_score import load_initial_exam_scores, project_initial_exam_score
from apps.domains.results.services.omr_subjective_completion import pending_omr_result_ids
from apps.domains.students.models import ClassGroup


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
            tenant=tenant, name="다원 체험 강사", phone="", is_manager=False,
            user__tenant=tenant, user__username="t719_dawon-demo-employee",
            user__is_staff=False, user__is_superuser=False, is_active=True,
        )

    def get(self, request):
        tenant = request.tenant
        groups = {
            group.pk: {"id": group.pk, "name": group.name, "members": group.member_count, "courses": []}
            for group in ClassGroup.objects.filter(tenant=tenant).annotate(
                member_count=Count("students", filter=Q(students__tenant=tenant, students__deleted_at__isnull=True)),
            ).order_by("name", "pk")
        }
        courses = Lecture.objects.filter(tenant=tenant, is_system=False).annotate(
            students=Count("enrollments", filter=Q(
                enrollments__status="ACTIVE", enrollments__tenant=tenant,
                enrollments__student__tenant=tenant, enrollments__student__deleted_at__isnull=True,
            ), distinct=True),
            lessons=Count("sessions", distinct=True),
        ).values("id", "title", "is_active", "students", "lessons", "class_group_link__group_id").order_by("title", "pk")
        for course in courses:
            group_id = course.pop("class_group_link__group_id")
            if group_id not in groups:
                group_id = None
                groups.setdefault(None, {"id": None, "name": "미편성 강의", "members": None, "courses": []})
            groups[group_id]["courses"].append(course)

        exams = Exam.objects.filter(
            tenant=tenant, exam_type=Exam.ExamType.REGULAR, is_active=True,
            sessions__lecture__tenant=tenant,
        ).distinct().order_by("title", "pk")
        linked_exam = Exam.objects.filter(
            tenant=tenant, pk=OuterRef("target_id"), sessions__lecture_id=OuterRef("enrollment__lecture_id"),
        )
        snapshots = Result.objects.filter(
            target_type="exam", target_id__in=exams.values("pk"), enrollment__tenant=tenant,
            enrollment__lecture__tenant=tenant, enrollment__student__tenant=tenant,
        ).filter(Exists(linked_exam))
        results = list(snapshots)
        pending = pending_omr_result_ids(results)
        initial = load_initial_exam_scores(
            exam_ids={row.target_id for row in results}, enrollment_ids={row.enrollment_id for row in results},
        )
        by_exam = defaultdict(list)
        for row in results:
            if row.pk in pending:
                continue
            score = project_initial_exam_score(
                state=initial.get((row.target_id, row.enrollment_id)),
                fallback_score=row.total_score, fallback_max_score=row.max_score,
            )
            if not score.not_submitted and score.total_score is not None:
                by_exam[row.target_id].append(score.total_score)
        response = Response({
            "groups": list(groups.values()), "employee": self.employee(tenant).values("name", "position").first(),
            "exams": [{
                "id": exam.pk, "title": exam.title, "graded": len(by_exam[exam.pk]),
                "average": fmean(by_exam[exam.pk]) if by_exam[exam.pk] else None,
            } for exam in exams.only("pk", "title")],
        })
        response["Cache-Control"] = "no-store"
        return response

    def patch(self, request):
        if not isinstance(request.data, dict) or set(request.data) != {"position"} or request.data["position"] not in ("INSTRUCTOR", "ASSISTANT"):
            raise ValidationError("강사 또는 조교 직위를 선택해 주세요.")
        if not self.employee(request.tenant).update(position=request.data["position"]):
            raise NotFound()
        return Response({"position": request.data["position"]})
