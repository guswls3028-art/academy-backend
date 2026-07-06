from __future__ import annotations

import inspect

from django.test import SimpleTestCase

from apps.domains.attendance.views import AttendanceViewSet


class AttendanceRaceGuardTests(SimpleTestCase):
    def test_status_mutations_use_row_locks(self):
        queryset_source = inspect.getsource(AttendanceViewSet.get_queryset)
        bulk_source = inspect.getsource(AttendanceViewSet.bulk_set_present)

        self.assertIn("select_for_update", queryset_source)
        self.assertIn("select_for_update", bulk_source)
