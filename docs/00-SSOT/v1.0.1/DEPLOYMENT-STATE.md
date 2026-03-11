# V1.0.1 Deployment State

**Snapshot Date:** 2026-03-11
**Status:** DEPLOYED & VERIFIED

---

## 1. Git State

### Frontend
- **Repo:** `guswls3028-art/academy-frontend`
- **Branch:** `main`
- **Commit:** `56bec96f` — `fix: V1.0.1 품질 감사 — alert→feedback 전환, 학습관리 탭 분리, UX 개선`
- **Files:** 1,072
- **Deploy method:** Cloudflare Pages (auto-deploy on push to main)

### Backend
- **Repo:** `guswls3028-art/academy-backend`
- **Branch:** `main`
- **Commit:** `c033877b` — `fix: 메시징 템플릿·서비스·워커 정리 및 개선`
- **Files:** 2,277
- **Deploy method:** GitHub Actions → ECR → ASG Instance Refresh

---

## 2. Infrastructure

### API Server
- **ALB:** `academy-v1-api-alb`
- **ASG:** `academy-v1-api-asg` (min=1, desired=1, max=2)
- **Container:** `academy-api` (arm64, :latest tag)
- **Domain:** `api.hakwonplus.com`

### Frontend CDN
- **Provider:** Cloudflare Pages
- **Domains:** `hakwonplus.com`, `tchul.com`, + tenant subdomains
- **Build:** Vite (React + TypeScript SPA)

### Workers
- **Messaging SQS:** `academy-v1-messaging-queue`
- **AI SQS:** `academy-v1-ai-queue`
- **Video Batch:** AWS Batch (academy-v1-video-batch)

### Database
- **Engine:** PostgreSQL (multi-tenant)
- **Status:** Connected (verified via /health endpoint)

---

## 3. Health Check Results (2026-03-11)

```
GET /healthz → 200
GET /health  → 200 {"status":"healthy","service":"academy-api","database":"connected"}
GET https://hakwonplus.com → 200
GET https://tchul.com → 200
```

---

## 4. Tenant Registry

| ID | Code | Domain | Theme |
|----|------|--------|-------|
| 1 | hakwonplus | hakwonplus.com | common |
| 2 | tchul | tchul.com | tchul |
| 3 | limglish | (subdomain) | common |
| 4 | ymath | (subdomain) | common |
| 9999 | common | localhost dev | common |
