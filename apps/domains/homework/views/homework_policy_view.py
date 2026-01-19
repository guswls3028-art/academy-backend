from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated

from apps.domains.homework.models import HomeworkPolicy
from apps.domains.homework.serializers import HomeworkPolicySerializer
from apps.domains.results.permissions import IsTeacherOrAdmin


class HomeworkPolicyViewSet(ModelViewSet):
    """
    HomeworkPolicy API (Admin/Teacher)

    GET /homework/policies/?session=
    PATCH /homework/policies/{id}/
    """

    queryset = HomeworkPolicy.objects.select_related("session", "session__lecture")
    serializer_class = HomeworkPolicySerializer
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    filterset_fields = ["session"]
