# PATH: apps/domains/fees/urls.py

from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"templates", views.FeeTemplateViewSet, basename="fee-template")
router.register(r"student-fees", views.StudentFeeViewSet, basename="student-fee")
router.register(r"invoices", views.StudentInvoiceViewSet, basename="student-invoice")
router.register(r"payments", views.FeePaymentViewSet, basename="fee-payment")

urlpatterns = [
    # Dashboard
    path("dashboard/", views.FeeDashboardView.as_view(), name="fee-dashboard"),
    path("dashboard/overdue/", views.FeeOverdueView.as_view(), name="fee-overdue"),
] + router.urls
