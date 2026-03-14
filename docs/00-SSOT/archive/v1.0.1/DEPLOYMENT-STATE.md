# V1.0.1 Deployment State (Final)

**Snapshot Date:** 2026-03-11
**Status:** DEPLOYED & VERIFIED (Final Image)

---

## 1. Git State

### Frontend
- **Repo:** `guswls3028-art/academy-frontend`
- **Branch:** `main`
- **Commit:** `5ce0233a` — `fix: --stu-radius 토큰 정의 + PlayerToast 자동닫힘 수정`
- **Deploy method:** Cloudflare Pages (auto-deploy on push to main)

### Backend
- **Repo:** `guswls3028-art/academy-backend`
- **Branch:** `main`
- **Commit:** `334270bc` — `fix: 추가 테넌트 격리 + VideoProcessingComplete 보안 강화`
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
- **Domains:** `hakwonplus.com`, `tchul.com`, `limglish.kr` + tenant subdomains
- **Build:** Vite (React + TypeScript SPA)

### Workers
- **Messaging SQS:** `academy-v1-messaging-queue`
- **AI SQS:** `academy-v1-ai-queue`
- **Video Batch:** AWS Batch (academy-v1-video-batch)

### Database
- **Engine:** PostgreSQL (multi-tenant)
- **Status:** Connected (verified via /health endpoint)

---

## 3. Health Check Results (2026-03-11, Final)

```
GET /healthz → 200
GET /health  → 200 {"status":"healthy","service":"academy-api","database":"connected"}
GET https://hakwonplus.com → 200
GET https://tchul.com → 200
GET https://limglish.kr → 200
```

---

## 4. Tenant Registry

| ID | Code | Domain | Theme |
|----|------|--------|-------|
| 1 | hakwonplus | hakwonplus.com | common |
| 2 | tchul | tchul.com | tchul |
| 3 | limglish | limglish.kr | common |
| 4 | ymath | (subdomain) | ymath |
| 9999 | common | localhost dev | common |
