"""Native nonlecture operations available to the public Dawon sample owner."""

import re

from apps.domains.clinic.dawon_demo import is_demo_tenant


_CLINIC_SETTINGS = "/api/v1/clinic/settings/"
_CLINIC_SETTING_FIELDS = {
    "colors",
    "use_daily_random",
    "auto_approve_booking",
    "booking_mode",
    "booking_interval_minutes",
    "booking_max_stay_minutes",
}
_BUNDLE_LIST = "/api/v1/exams/bundles/"
_BUNDLE_DETAIL = re.compile(r"/api/v1/exams/bundles/[1-9][0-9]{0,17}/")
_BUNDLE_APPLY = re.compile(r"/api/v1/exams/bundles/[1-9][0-9]{0,17}/apply/")


def allows_nonlecture_operation(request):
    """Called after the token is identified as a Dawon demo admin token."""
    if not is_demo_tenant(getattr(request, "tenant", None)):
        return False
    method, path = request.method, request.path
    if path == _CLINIC_SETTINGS:
        if method in {"GET", "HEAD"}:
            return True
        return (
            method == "PATCH"
            and isinstance(request.data, dict)
            and bool(request.data)
            and request.data.keys() <= _CLINIC_SETTING_FIELDS
        )
    # The native bundle view rechecks staff membership and tenant ownership
    # for the bundle, its templates, its enrollments and the target session.
    if method == "POST":
        return path == _BUNDLE_LIST or bool(_BUNDLE_APPLY.fullmatch(path))
    return method in {"PUT", "DELETE"} and bool(_BUNDLE_DETAIL.fullmatch(path))
