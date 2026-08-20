from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.domains.results.views.wrong_note_pdf_view import WrongNotePDFCreateView


class WrongNotePDFRequestBoundaryTests(SimpleTestCase):
    def test_zero_start_order_is_not_replaced_by_the_default(self):
        request = SimpleNamespace(
            data={"enrollment_id": 1, "from_session_order": 0},
        )

        with self.assertRaises(ValidationError):
            WrongNotePDFCreateView().post(request)

    def test_zero_scope_ids_are_not_treated_as_missing(self):
        view = WrongNotePDFCreateView()
        request = SimpleNamespace(data={"lecture_id": 0})
        enrollment = SimpleNamespace(lecture_id=1)

        with self.assertRaises(ValidationError):
            view._validate_scope_ids(request, enrollment)
