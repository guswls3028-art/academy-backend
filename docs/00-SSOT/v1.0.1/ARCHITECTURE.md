# V1.0.1 Architecture Overview

**Snapshot Date:** 2026-03-11

---

## 1. System Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Cloudflare CDN                     │
│  hakwonplus.com / tchul.com / tenant subdomains      │
│  → Cloudflare Pages (React SPA, auto-deploy on push) │
└───────────────┬─────────────────────────────────────┘
                │ HTTPS
                ▼
┌─────────────────────────────────────────────────────┐
│              api.hakwonplus.com                       │
│              AWS ALB (academy-v1-api-alb)             │
│              Health: /healthz (liveness)              │
└───────────────┬─────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────┐
│           ASG: academy-v1-api-asg                    │
│           EC2 (arm64) × 1~2 instances                │
│           Docker: academy-api:latest                  │
│           Django + Gunicorn + DRF                     │
│                                                       │
│  ┌─────────┐  ┌──────────┐  ┌───────────────────┐   │
│  │ /healthz│  │ /health  │  │ /api/v1/...       │   │
│  │ liveness│  │ readiness│  │ REST API endpoints│   │
│  └─────────┘  └──────────┘  └───────────────────┘   │
└───────────────┬──────────┬──────────────────────────┘
                │          │
         ┌──────┘          └──────┐
         ▼                        ▼
┌─────────────────┐    ┌──────────────────────┐
│  PostgreSQL      │    │  AWS SQS              │
│  Multi-tenant    │    │  messaging-queue      │
│  (RDS)           │    │  ai-queue             │
└─────────────────┘    └─────────┬────────────┘
                                  │
                                  ▼
                       ┌──────────────────────┐
                       │  SQS Workers          │
                       │  (messaging, AI)      │
                       └──────────────────────┘

┌─────────────────────────────────────────────────────┐
│  AWS Batch: academy-v1-video-batch                   │
│  Video Processing Worker                              │
│  Triggered by: SQS / API                             │
│  → HLS transcode → S3 → CloudFront CDN              │
└─────────────────────────────────────────────────────┘
```

---

## 2. Frontend Architecture

```
frontend/src/
├── app/                    # App shell, routing, providers
│   ├── router/
│   │   ├── AdminRouter.tsx    # Admin SPA routes (50+ routes)
│   │   ├── AppRouter.tsx      # Root router (admin/student/promo/auth)
│   │   └── AuthRouter.tsx     # Login/register routes
│   └── providers/
│       └── QueryProvider.tsx   # React Query client config
│
├── features/               # Admin feature modules
│   ├── auth/               # Login, JWT, AuthContext
│   ├── lectures/           # Lecture CRUD, sessions, scores
│   ├── students/           # Student management
│   ├── exams/              # Exam management, OMR
│   ├── results/            # Grade management
│   ├── videos/             # Video management, upload
│   ├── community/          # Board, QnA, notices, counsel
│   ├── messages/           # SMS messaging, auto-send
│   ├── clinic/             # Clinic booking, operations
│   ├── staff/              # Staff management, operations
│   ├── storage/            # File storage (my/student)
│   ├── materials/          # Teaching materials, OMR sheets
│   ├── homework/           # Homework management
│   ├── settings/           # Org settings, profile, appearance
│   └── dashboard/          # Admin dashboard
│
├── student/                # Student mobile-first SPA
│   ├── app/
│   │   └── StudentRouter.tsx  # Student routes (30+ routes)
│   ├── domains/            # Student feature modules
│   │   ├── dashboard/
│   │   ├── video/          # HLS player, course cards
│   │   ├── exams/
│   │   ├── grades/
│   │   ├── sessions/
│   │   ├── clinic/
│   │   ├── community/
│   │   ├── notifications/
│   │   ├── profile/
│   │   └── ...
│   └── shared/             # Student-specific shared UI
│       └── ui/
│           ├── layout/     # StudentLayout, TabBar, TopBar
│           ├── feedback/   # studentToast (V1.0.1 new)
│           └── theme/      # CSS tokens, tenant themes
│
├── shared/                 # Cross-cutting shared code
│   ├── ui/
│   │   ├── ds/             # Design system components
│   │   ├── domain/         # DomainLayout, DomainPanel, tabs
│   │   ├── modal/          # AdminModal system
│   │   ├── feedback/       # feedback toast (antd-based)
│   │   └── editor/         # RichTextEditor
│   ├── api/                # Axios instance, interceptors
│   ├── tenant/             # Tenant resolution, branding
│   └── hooks/              # Shared hooks
│
└── promo/                  # Marketing/promo site
```

---

## 3. Backend Architecture

```
backend/
├── apps/
│   ├── api/                # Django project root
│   │   ├── config/
│   │   │   ├── settings/
│   │   │   │   ├── base.py     # Common settings
│   │   │   │   ├── prod.py     # Production (ALLOWED_HOSTS, CORS)
│   │   │   │   ├── local.py    # Local development
│   │   │   │   └── worker.py   # Worker process settings
│   │   │   ├── urls.py         # URL routing
│   │   │   └── wsgi.py
│   │   └── middleware/
│   │       └── tenant.py       # Tenant resolution middleware
│   │
│   ├── core/               # Auth, tenants, permissions
│   │   ├── models.py       # Tenant, User, Membership
│   │   ├── auth/           # JWT, login/register views
│   │   └── permissions.py  # Role-based access control
│   │
│   ├── domains/            # Business domain apps (21 apps)
│   │   ├── students/       # Student CRUD, enrollment
│   │   ├── lectures/       # Lectures, sessions
│   │   ├── exams/          # Exam CRUD, scoring, OMR
│   │   ├── results/        # Grade aggregation
│   │   ├── attendance/     # Attendance tracking
│   │   ├── homework/       # Homework management
│   │   ├── clinic/         # Clinic scheduling
│   │   ├── community/      # Board, QnA, notices
│   │   ├── inventory/      # File storage
│   │   ├── assets/         # OMR PDF generation
│   │   ├── submissions/    # Exam/homework submissions
│   │   └── ...
│   │
│   └── worker/             # Async workers
│       └── video_worker/   # AWS Batch video processing
│
├── docker/                 # Dockerfile, compose
├── scripts/v1/             # Deploy scripts (deploy.ps1, etc.)
├── docs/00-SSOT/           # Documentation (this folder)
└── .github/workflows/      # CI/CD pipelines
```

---

## 4. Key Design Decisions

### Multi-Tenant Isolation (CRITICAL)
- Tenant resolved from request domain/header via middleware
- All querysets filtered by `tenant_id`
- Cross-tenant data access is architecturally impossible
- Tenant context propagated to all service layers

### Authentication
- JWT (access + refresh tokens)
- Role-based: admin, teacher, student, parent
- Parent accounts can view linked student data (read-only)

### Video Pipeline
- Upload → S3 presigned URL → SQS message → AWS Batch job
- Batch job: FFmpeg HLS transcode → S3 output → CloudFront CDN
- Status tracking: PENDING → PROCESSING → COMPLETE / FAILED
- Retry mechanism with exponential backoff

### Frontend State Management
- React Query (TanStack Query) for server state
- No Redux/Zustand — React Query + URL state
- Query key conventions: `["domain", "resource", ...params]`
- Default stale time: 10s (student), varies by feature (admin)
