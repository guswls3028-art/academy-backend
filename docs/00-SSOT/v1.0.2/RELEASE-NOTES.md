# V1.0.2 Release Notes (SEALED — 2026-03-12)

## Version Status: SEALED
This version is locked. No further changes to V1.0.2 codebase.
Next version: V1.1.0 (무중단 배포 인프라 전환 예정)

---

## 1. Subscription / Payment System

### Backend
- **Program model** extended with subscription lifecycle:
  - `subscription_status`: active / expired / grace / cancelled
  - `subscription_started_at`, `subscription_expires_at`: DateField
  - `billing_email`: EmailField
  - `is_subscription_active` property: checks status + expiry date
  - `days_remaining` property: remaining days (None = unlimited)
- **TenantMiddleware** subscription enforcement:
  - Checks `_check_subscription()` after tenant resolution
  - Returns **402 Payment Required** for expired subscriptions
  - Exempt paths: `/api/v1/token/`, `/api/v1/auth/`, `/api/v1/core/me`, `/api/v1/core/program`, `/api/v1/core/subscription`, `/admin/`, `/static/`, `/media/`, student registration/password paths
  - Subscription check failure falls through (service availability priority)
- **SubscriptionView** (`GET /api/v1/core/subscription/`):
  - AllowAny + TenantResolved permissions
  - Returns: plan, status, dates, days_remaining, billing_email, tenant info
- **Data migration** (0015):
  - Tenants 1, 2, 9999 → Premium plan, 9999 days
  - All other tenants → Basic plan, March 13, 2026 start, April 12 expiry

### Frontend
- **BillingSettingsPage**: plan badge, subscription status card, remaining days, expired warning banner
- **SubscriptionExpiredOverlay**: full-screen blocking modal on 402 response
  - Listens to `subscription-expired` CustomEvent from axios interceptor
  - Displays in both admin app (App.tsx) and student app (StudentApp.tsx)
  - "로그인 페이지로 이동" button clears tokens
- **Settings sidebar**: billing tab added (FiCreditCard icon)

---

## 2. Video Social Features

### Backend
- **VideoLike** model: unique constraint (video, student), tenant_id isolation
  - `StudentVideoLikeView`: POST toggle, updates denormalized `like_count` via F()
- **VideoComment** model: dual-author (student/staff), 1-level threading (parent FK), soft delete
  - `StudentVideoCommentListView`: GET threaded (100 limit) + POST create
  - `StudentVideoCommentDetailView`: PATCH edit (own only) + DELETE soft delete (own only)
- **View count**: F() expression increment on playback start (no race condition)
- **Video model** additions: `view_count`, `like_count`, `comment_count` (denormalized)

### Frontend
- **VideoCommentSection** component:
  - Comment list with threading (1 level replies)
  - Teacher badge ("선생님" tag in primary color)
  - Avatar component with photo or initial fallback
  - Edit/delete mutations with optimistic UI
  - Reply-to state management
  - Enter to submit, Escape to cancel edit
- **LikeButton** component:
  - Heart SVG with fill animation
  - Optimistic toggle (instant visual feedback)
  - Denormalized count display
- **Video info section** enhanced:
  - View count (Korean locale: "조회수 1,234회" / "1.2만회")
  - Relative upload date (timeAgo: "방금", "3분 전", "2시간 전")
  - Duration display
- **timeAgo.ts** + **formatViewCount** utilities

---

## 3. Student Profile Photo Upload

### Frontend
- **ProfilePage** photo upload UI:
  - Circular avatar (80px) with camera overlay on hover
  - Hidden file input, triggered by click
  - Image type validation, 5MB size limit
  - Invalidates `["student", "me"]` query on success

---

## 4. Staff Profile Photo

### Backend
- `Staff.profile_photo` ImageField (upload_to="staff_profile/%Y/%m/")
- `profile_photo_url` SerializerMethodField on StaffListSerializer + StaffDetailSerializer
- Migration: 0005_staff_profile_photo

---

## 5. Duplicate Student Name Numbering

### Frontend
- `applyDisplayNames()` utility in students API:
  - Groups students by name
  - Assigns number suffixes by id ascending (earlier = lower number)
  - Only applies when 2+ students share the same name
- Applied across all student name display surfaces:
  - StudentsTable, StudentsHomePage (delete confirmation)
  - EnrollStudentModal, SessionEnrollModal
  - LectureStudentsPage (local computation from attendance matrix)

---

## 6. Migrations

| # | App | Name | Type |
|---|-----|------|------|
| 0014 | core | program_billing_email_and_more | Schema: subscription fields |
| 0015 | core | set_subscription_for_all_tenants | Data: initial subscription setup |
| 0005 | staffs | staff_profile_photo | Schema: profile_photo field |
| 0008 | video | videocomment_videolike_and_more | Schema: social models + counters |

---

## 7. Tenant Isolation Audit

All new features maintain strict tenant isolation:
- VideoLike, VideoComment: `tenant_id` field with db_index
- All student_app views filter by `student.tenant_id`
- Subscription check operates within resolved tenant context
- No cross-tenant fallback or mixing

---

## 8. Commits

### Round 1 — Features
- **Backend**: `3f8f8a71` — feat: V1.0.2 (subscription, video social, staff photo, docs)
- **Frontend**: `474a1f1f` — feat: V1.0.2 (billing page, overlay, comments, like, profile photo)

### Round 2 — Audit Fixes
- **Backend**: `2e9fcf37` — fix: tenant isolation, race condition, N+1 query
- **Frontend**: `adcf1be6` — fix: invalid date validation in BillingSettingsPage

---

## 9. Next Version: V1.1.0

### TODO (Infrastructure)
- 무중단 배포 (Zero-downtime deployment) 전환
- Rolling update / Blue-Green deployment 구현
- Database migration strategy for zero-downtime
- Health check integration with deployment pipeline
