# Subscription System Documentation (V1.0.2)

## Architecture

```
Request Flow:
  Client → TenantMiddleware
    → resolve_tenant()
    → _check_subscription(tenant, request)
      → program.is_subscription_active?
        → True: continue to view
        → False: return 402 Payment Required
    → view handler
```

## Program Model Fields

| Field | Type | Description |
|-------|------|-------------|
| plan | CharField | lite / basic / premium |
| monthly_price | PositiveIntegerField | Auto-synced from PLAN_PRICES |
| subscription_status | CharField | active / expired / grace / cancelled |
| subscription_started_at | DateField | Subscription start date |
| subscription_expires_at | DateField | Expiry date (null = unlimited) |
| billing_email | EmailField | Payment notification email |

## Subscription Status Logic

```python
is_subscription_active:
  if status in (ACTIVE, GRACE):
    if expires_at is None: return True  # unlimited
    return date.today() <= expires_at
  return False  # expired, cancelled
```

## Exempt Paths (Subscription Check Bypass)

These paths are never blocked even when subscription is expired:
- `/api/v1/token/` — JWT login/refresh
- `/api/v1/auth/` — authentication
- `/api/v1/students/registration` — student self-registration
- `/api/v1/students/password` — password find/reset
- `/api/v1/core/me` — user profile
- `/api/v1/core/program` — program info (login page needs this)
- `/api/v1/core/subscription` — subscription info (billing page needs this)
- `/admin/` — Django admin
- `/static/`, `/media/` — static files

## Plan Pricing

| Plan | Monthly Price (KRW) |
|------|-------------------|
| Lite | 55,000 |
| Basic | 150,000 |
| Premium | 300,000 |

## Initial Data (Migration 0015)

| Tenant ID | Plan | Expires |
|-----------|------|---------|
| 1 | Premium | 2053-07-30 (9999 days) |
| 2 | Premium | 2053-07-30 (9999 days) |
| 9999 | Premium | 2053-07-30 (9999 days) |
| All others | Basic | 2026-04-12 (1 month) |

## API Endpoint

```
GET /api/v1/core/subscription/
Permission: AllowAny + TenantResolved

Response:
{
  "plan": "basic",
  "plan_display": "Basic",
  "monthly_price": 150000,
  "subscription_status": "active",
  "subscription_status_display": "활성",
  "subscription_started_at": "2026-03-13",
  "subscription_expires_at": "2026-04-12",
  "is_subscription_active": true,
  "days_remaining": 31,
  "billing_email": "",
  "tenant_code": "example",
  "tenant_name": "Example Academy"
}
```

## Frontend Handling

1. **402 Response** → Axios interceptor dispatches `subscription-expired` CustomEvent
2. **SubscriptionExpiredOverlay** catches event → shows blocking modal
3. **BillingSettingsPage** queries `/core/subscription/` for billing UI
4. Modal offers "로그인 페이지로 이동" (clears tokens, redirects)
