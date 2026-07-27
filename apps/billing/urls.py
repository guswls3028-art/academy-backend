from django.urls import path

from apps.billing.views import (
    # 플랫폼 관리자
    AdminTenantSubscriptionListView,
    AdminExtendSubscriptionView,
    AdminInvoiceListView,
    AdminInvoiceDetailView,
    AdminMarkInvoicePaidView,
    AdminBankTransferNoticeListView,
    AdminBankTransferNoticeConfirmView,
    AdminBankTransferNoticeRejectView,
    AdminTaxInvoiceMarkIssuedView,
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
    MyBusinessProfileView,
    MyBankTransferSummaryView,
    MyBankTransferActivateView,
    MyBankTransferNoticeSubmitView,
    CancelSubscriptionView,
    RevokeCancelView,
    # Toss 웹훅 (공개)
    TossWebhookView,
)

urlpatterns = [
    # ── 플랫폼 관리자 (Superuser) ──
    path("admin/tenants/", AdminTenantSubscriptionListView.as_view(), name="billing-admin-tenants"),
    path("admin/tenants/<int:program_id>/extend/", AdminExtendSubscriptionView.as_view(), name="billing-admin-extend"),
    path("admin/invoices/", AdminInvoiceListView.as_view(), name="billing-admin-invoices"),
    path("admin/invoices/<int:pk>/", AdminInvoiceDetailView.as_view(), name="billing-admin-invoice-detail"),
    path("admin/invoices/<int:pk>/mark-paid/", AdminMarkInvoicePaidView.as_view(), name="billing-admin-mark-paid"),
    path("admin/bank-transfer/notices/", AdminBankTransferNoticeListView.as_view(), name="billing-admin-bank-transfer-notices"),
    path("admin/bank-transfer/notices/<int:pk>/confirm/", AdminBankTransferNoticeConfirmView.as_view(), name="billing-admin-bank-transfer-confirm"),
    path("admin/bank-transfer/notices/<int:pk>/reject/", AdminBankTransferNoticeRejectView.as_view(), name="billing-admin-bank-transfer-reject"),
    path("admin/tax-invoices/<int:pk>/mark-issued/", AdminTaxInvoiceMarkIssuedView.as_view(), name="billing-admin-tax-invoice-mark-issued"),
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
    path("business-profile/", MyBusinessProfileView.as_view(), name="billing-business-profile"),
    path("bank-transfer/", MyBankTransferSummaryView.as_view(), name="billing-bank-transfer"),
    path("bank-transfer/activate/", MyBankTransferActivateView.as_view(), name="billing-bank-transfer-activate"),
    path("bank-transfer/notices/", MyBankTransferNoticeSubmitView.as_view(), name="billing-bank-transfer-notices"),
    path("cancel/", CancelSubscriptionView.as_view(), name="billing-cancel"),
    path("cancel/revoke/", RevokeCancelView.as_view(), name="billing-cancel-revoke"),

    # ── Toss 웹훅 (공개, 결제 재조회 / 빌링키 fingerprint 검증) ──
    path("webhooks/toss/", TossWebhookView.as_view(), name="billing-webhook-toss"),
]
