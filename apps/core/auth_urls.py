# PATH: apps/core/auth_urls.py

from django.urls import path

from apps.core.views.account_recovery import AccountRecoveryDispatchView


urlpatterns = [
    path(
        "account-recovery/dispatch/",
        AccountRecoveryDispatchView.as_view(),
        name="auth-account-recovery-dispatch",
    ),
]
