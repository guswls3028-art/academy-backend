# PATH: apps/domains/homework/views/__init__.py

from .homework_score_viewset import HomeworkScoreViewSet
from .homework_policy_viewset import HomeworkPolicyViewSet

__all__ = [
    "HomeworkScoreViewSet",
    "HomeworkPolicyViewSet",
]
