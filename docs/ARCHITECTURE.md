# Production Architecture

## Overview

Production-grade architecture for Korea-focused multi-tenant education SaaS.

**Core Principles:**
- Correctness (tenant isolation)
- Reliability
- Scalability (10k DAU target)
- Cost efficiency
- Clean migration path (EC2 → ECS)

**Current Implementation:**
- SQS-only queue system (Redis removed)
- PostgreSQL-only data layer (Redis removed)
- Stateless services
- Docker containerization
- pnpm-based frontend builds

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    CloudFront CDN                           │
│              (Static Assets + Media CDN)                    │
└───────────────────────┬─────────────────────────────────────┘
                        │
        ┌───────────────┴───────────────┐
        │                               │
        ▼                               ▼
┌───────────────┐            ┌──────────────────┐
│   Frontend    │            │   API Server      │
│   (S3 Static) │            │   (EC2/ECS)       │
│               │            │                   │
│  React/Vite   │            │  Django API       │
│  pnpm build   │            │  Container        │
└───────────────┘            └─────────┬─────────┘
                                       │
                    ┌──────────────────┼──────────────────┐
                    │                  │                  │
                    ▼                  ▼                  ▼
            ┌──────────────┐                    ┌──────────────┐
            │ RDS Postgres │                    │ S3 Bucket    │
            │ (Single AZ)  │                    │  (Media)     │
            └──────────────┘                    └──────────────┘
                    │
                    │
                    ▼
        ┌───────────────────────────────────────┐
        │      SQS Queues (Tier-based)            │
        │                                       │
        │  video-queue  ai-lite-queue          │
        │  ai-basic-queue  ai-premium-queue    │
        └───────────────────────────────────────┘
                    │
        ┌───────────┼───────────┐
        │           │           │
        ▼           ▼           ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│video-worker │ │ai-worker-cpu│ │ai-worker-gpu│
│  (CPU)      │ │  (CPU)      │ │  (GPU)      │
│             │ │ Lite+Basic  │ │ Premium     │
│             │ │             │ │ (Future)    │
└─────────────┘ └─────────────┘ └─────────────┘
```

## Key Design Decisions

### 1. Multi-Tenant Hardening

**Implementation:**
- All tenant-scoped tables: `tenant_id NOT NULL`
- Indexes: `(tenant_id)`, `(tenant_id, created_at)`
- UNIQUE constraints: `UNIQUE(tenant_id, phone)` (NOT email)
- Query-level filter enforcement
- Phone-based identity (NOT email)

**Validation:**
- Automated audit reports
- Query logging to detect missing filters

### 2. Asynchronous Job Processing

**Implementation:**
- API only enqueues to SQS and returns immediately
- All heavy processing handled by Workers
- SQS Long Polling (20 seconds)
- DLQ for failed job isolation
- Idempotent processing

**Queue Structure:**
- Video: `academy-video-jobs` + DLQ
- AI Lite: `academy-ai-jobs-lite` + DLQ
- AI Basic: `academy-ai-jobs-basic` + DLQ
- AI Premium: `academy-ai-jobs-premium` + DLQ

### 3. Tier-Based Routing

**Implementation:**
- Lite: CPU OCR only (welfare tier)
- Basic: CPU-based OMR/status detection + improved OCR
- Premium: GPU-based full OCR (future)

**Fairness:**
- Weighted Polling (Basic 3:1 Lite) in CPU worker
- Separate worker pools for Premium (future)

**Tier Limits:**
- Lite: OCR only
- Basic: OCR + OMR/status detection
- Premium: All job types

### 4. Video Processing Optimization

**Implementation:**
- Low FPS sampling (1-3 fps)
- Page transition detection (SSIM/Frame Diff)
- Extract only 1 representative frame per page
- Max page limit (15 pages)
- Hard timeout enforcement

### 5. Stateless Design

**Implementation:**
- All services stateless
- No local disk dependencies
- S3 only (file storage)
- DB-based session management (Redis removed)
- No in-memory sessions

**Benefits:**
- Easy horizontal scaling
- ECS/Fargate compatible
- High availability

### 6. Database-Only Architecture

**Redis Removal:**
- Session management: DB-based (`VideoPlaybackSession` model). For video playback/access details, see **ARCH_VIDEO_SSOT.md**.
- Rate limiting: DB-based (or ALB-level for 10k DAU)
- Caching: Not used (application-level if needed)
- Queue: SQS only

**Benefits:**
- Simpler architecture
- Lower cost
- Easier operations
- Sufficient for 500 DAU, scalable to 10k DAU

## Component Descriptions

### Frontend
- **Technology**: React + Vite
- **Build**: pnpm
- **Hosting**: S3 Static Website + CloudFront
- **Deployment**: CI/CD pipeline

### API Server
- **Technology**: Django REST Framework
- **Server**: Gunicorn
- **Deployment**: Docker container (EC2 or ECS Fargate)
- **Scaling**: Horizontal (stateless)

### Database
- **Technology**: PostgreSQL (RDS)
- **Initial**: db.t4g.micro (Single AZ)
- **Target**: db.t4g.medium (Multi-AZ for 10k DAU)
- **Connection Pooling**: PgBouncer (recommended for 10k DAU)

### Storage
- **Media**: S3 bucket (`academy-media-prod`)
- **CDN**: CloudFront distribution
- **Lifecycle**: IA after 30 days, Glacier after 90 days

### Queue System
- **Technology**: AWS SQS
- **Queues**: Video + AI (Lite/Basic/Premium)
- **DLQ**: One per queue
- **Long Polling**: 20 seconds
- **Visibility Timeout**: Job-dependent

### Workers

#### Video Worker
- **Type**: CPU-based
- **Processing**: Video frame extraction, thumbnail generation
- **Queue**: `academy-video-jobs`
- **Deployment**: Docker container (EC2 or ECS Fargate Spot)

#### AI Worker CPU
- **Type**: CPU-based
- **Processing**: Lite + Basic tier jobs
- **Queues**: `academy-ai-jobs-lite`, `academy-ai-jobs-basic`
- **Polling**: Weighted (Basic 3:1 Lite)
- **Deployment**: Docker container (EC2 or ECS Fargate Spot)

#### AI Worker GPU (Future)
- **Type**: GPU-based
- **Processing**: Premium tier jobs only
- **Queue**: `academy-ai-jobs-premium`
- **Deployment**: EC2 GPU instance (g4dn.xlarge)

## Performance Forecast

### Initial (500 DAU)

**Expected Load:**
- API requests: ~10,000/day
- AI jobs: ~100-200/day
- Video jobs: ~50-100/day

**Expected Response Times:**
- API: < 200ms (p95)
- AI jobs: 2-5 seconds
- Video jobs: 30-120 seconds

**Bottlenecks:**
- Low (current configuration sufficient)

### Target (10k DAU)

**Expected Load:**
- API requests: ~200,000/day
- AI jobs: ~2,000-5,000/day
- Video jobs: ~1,000-2,000/day

**Expected Response Times:**
- API: < 500ms (p95)
- AI jobs: 2-5 seconds (including queue wait)
- Video jobs: 30-120 seconds (including queue wait)

**Bottlenecks:**
- RDS connection count (Connection Pooling required)
- RDS CPU/IO (query optimization required)
- API server CPU (horizontal scaling needed)
- Worker throughput (horizontal scaling needed)

## Migration Path

### Current → ECS

**Code Changes:**
- Minimal (already stateless)

**Infrastructure Changes:**
- Task Definition creation
- Service creation
- ALB connection

**Estimated Effort:**
- Developer: 1-2 days
- Infrastructure Engineer: 3-5 days

## Next Steps

1. **Create SQS Queues**: Run `scripts/create_ai_sqs_resources.py` and `scripts/create_sqs_resources.py`
2. **Set Environment Variables**: Configure production environment variables
3. **Build Docker Images**: Build all services
4. **Deploy**: Deploy to EC2 or ECS
5. **Setup Monitoring**: Configure CloudWatch metrics and alerts
6. **Cost Monitoring**: Setup budget alerts
