from types import SimpleNamespace

from apps.domains.clinic.serializers import ClinicSessionSerializer


def test_clinic_session_status_summary_separates_pending_and_confirmed_bookings():
    session = SimpleNamespace(
        booked_count=3,
        pending_count=1,
        booked_confirmed_count=2,
        attended_count=4,
        no_show_count=5,
        cancelled_count=6,
    )

    assert ClinicSessionSerializer().get_status_summary(session) == {
        "pending": 1,
        "booked": 2,
        "reserved": 3,
        "attended": 4,
        "no_show": 5,
        "cancelled": 6,
    }
