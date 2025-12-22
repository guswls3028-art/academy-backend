# apps/domains/enrollment/filters.py

import django_filters

from .models import Enrollment


class EnrollmentFilter(django_filters.FilterSet):
    """
    Enrollment list filtering.
    Front uses: /lectures/enrollments/?lecture={lectureId}
    """

    class Meta:
        model = Enrollment
        fields = {
            "lecture": ["exact"],
            "student": ["exact"],
            "status": ["exact"],
        }
