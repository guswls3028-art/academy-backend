import django_filters
from .models import Attendance


class AttendanceFilter(django_filters.FilterSet):
    """
    Attendance 기본 필터
    - session 기준 조회 (프론트 필수)
    - enrollment 기준 조회 (확장 대비)
    - status: 출결 상태 필터 (출결 페이지 상태필터용)
    """

    session = django_filters.NumberFilter(field_name="session_id")
    enrollment = django_filters.NumberFilter(field_name="enrollment_id")
    status = django_filters.CharFilter(field_name="status")

    class Meta:
        model = Attendance
        fields = [
            "session",
            "enrollment",
            "status",
        ]
