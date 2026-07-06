"""Route-level clinic dependencies exposed to the results URL config."""

from __future__ import annotations

from apps.domains.clinic.views import ParticipantViewSet as AdminClinicBookingViewSet


__all__ = ["AdminClinicBookingViewSet"]
