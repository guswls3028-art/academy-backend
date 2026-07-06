# PATH: apps/domains/homework/views/__init__.py

from .homework_policy_viewset import HomeworkPolicyViewSet
from .homework_enrollment_view import HomeworkEnrollmentManageView

# HomeworkScoreViewSet은 homework_results 도메인 소유
# (URL prefix /homework/scores/ 는 호환을 위해 homework.urls 에서 라우팅)
from apps.support.homework.route_dependencies import HomeworkScoreViewSet

__all__ = [
    "HomeworkScoreViewSet",
    "HomeworkPolicyViewSet",
    "HomeworkEnrollmentManageView",
]
