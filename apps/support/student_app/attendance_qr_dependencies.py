"""Student routes for the attendance-owned QR write boundary."""

from apps.domains.attendance.qr_views import StudentQrRecordView, StudentQrResolveView

__all__ = ["StudentQrRecordView", "StudentQrResolveView"]
