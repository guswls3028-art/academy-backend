"""Route-level fee dependencies for the student app."""

from __future__ import annotations

from apps.domains.fees.views import (
    StudentFeeInvoiceDetailView,
    StudentFeeInvoiceListView,
    StudentFeePaymentListView,
)


__all__ = [
    "StudentFeeInvoiceDetailView",
    "StudentFeeInvoiceListView",
    "StudentFeePaymentListView",
]

