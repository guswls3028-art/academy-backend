from django.urls import path

from apps.billing.views import (
    # 플랫폼 관리자
    AdminTenantSubscriptionListView,
    AdminExtendSubscriptionView,
    AdminChangePlanView,
    AdminInvoiceListView,
    AdminInvoiceDetailView,
    AdminMarkInvoicePaidView,
    AdminDashboardView,
    # 원장 — 카드 등록
    CardRegisterPrepareView,
    CardRegisterCallbackView,
    CardDeleteView,
    # 원장 — 결제/구독
    MyInvoiceListView,
    MyInvoiceDetailView,
    MyBillingKeyListView,
    MyBillingProfileView,
    CancelSubscriptionView,
    RevokeCancelView,
    # Toss 웹훅 (공개)
    TossWebhookView,
)

urlpatterns = [
    # ── 플랫폼 관리자 (Superuser) ──
    path("admin/tenants/", AdminTenantSubscriptionListView.as_view(), name="billing-admin-tenants"),
    path("admin/tenants/<int:program_id>/extend/", AdminExtendSubscriptionView.as_view(), name="billing-admin-extend"),
    path("admin/tenants/<int:program_id>/change-plan/", AdminChangePlanView.as_view(), name="billing-admin-change-plan"),
    path("admin/invoices/", AdminInvoiceListView.as_view(), name="billing-admin-invoices"),
    path("admin/invoices/<int:pk>/", AdminInvoiceDetailView.as_view(), name="billing-admin-invoice-detail"),
    path("admin/invoices/<int:pk>/mark-paid/", AdminMarkInvoicePaidView.as_view(), name="billing-admin-mark-paid"),
    path("admin/dashboard/", AdminDashboardView.as_view(), name="billing-admin-dashboard"),

    # ── 원장: 카드 등록 ──
    path("card/register/prepare/", CardRegisterPrepareView.as_view(), name="billing-card-prepare"),
    path("card/register/callback/", CardRegisterCallbackView.as_view(), name="billing-card-callback"),
    path("cards/<int:pk>/", CardDeleteView.as_view(), name="billing-card-delete"),

    # ── 원장: 결제/구독 ──
    path("invoices/", MyInvoiceListView.as_view(), name="billing-invoices"),
    path("invoices/<int:pk>/", MyInvoiceDetailView.as_view(), name="billing-invoice-detail"),
    path("cards/", MyBillingKeyListView.as_view(), name="billing-cards"),
    path("profile/", MyBillingProfileView.as_view(), name="billing-profile"),
    path("cancel/", CancelSubscriptionView.as_view(), name="billing-cancel"),
    path("cancel/revoke/", RevokeCancelView.as_view(), name="billing-cancel-revoke"),

    # ── Toss 웹훅 (공개, 서명 검증) ──
    path("webhooks/toss/", TossWebhookView.as_view(), name="billing-webhook-toss"),
]
