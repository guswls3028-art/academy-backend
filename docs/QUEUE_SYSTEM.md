# Queue System

## Overview

The system uses AWS SQS exclusively for asynchronous job processing. Redis has been completely removed.

**Queue Architecture:**
- Video Queue: Video processing jobs
- AI Queues: 3-tier system (Lite, Basic, Premium)
- Dead Letter Queues: One per queue for failed jobs

## Queue Structure

### Video Queue

**Queue Name:** `academy-video-jobs`
**DLQ Name:** `academy-video-jobs-dlq`

**Message Format:**
```json
{
  "video_id": 123,
  "file_key": "videos/tenant-1/video-123.mp4",
  "tenant_code": "hakwonplus"
}
```

**Processing:**
- Frame extraction (low FPS sampling)
- Page transition detection
- Thumbnail generation
- Representative frame extraction

### AI Queues (3-Tier System)

#### Lite Queue
**Queue Name:** `academy-ai-jobs-lite`
**DLQ Name:** `academy-ai-jobs-lite-dlq`

**Allowed Job Types:**
- OCR only

**Worker:** AI Worker CPU (weighted polling)

#### Basic Queue
**Queue Name:** `academy-ai-jobs-basic`
**DLQ Name:** `academy-ai-jobs-basic-dlq`

**Allowed Job Types:**
- OCR
- OMR detection
- Status detection

**Worker:** AI Worker CPU (weighted polling, higher priority)

#### Premium Queue
**Queue Name:** `academy-ai-jobs-premium`
**DLQ Name:** `academy-ai-jobs-premium-dlq`

**Allowed Job Types:**
- All job types (full OCR, advanced analysis)

**Worker:** AI Worker GPU (future)

**Message Format (All AI Queues):**
```json
{
  "job_id": "uuid",
  "job_type": "OCR" | "OMR" | "STATUS_DETECTION",
  "tier": "lite" | "basic" | "premium",
  "payload": {...},
  "tenant_id": 1,
  "source_domain": "exams",
  "source_id": "123",
  "created_at": "2026-02-12T00:00:00Z",
  "attempt": 0
}
```

## Worker Architecture

### Video Worker

**Type:** CPU-based
**Queue:** `academy-video-jobs`
**Deployment:** Docker container (EC2 or ECS Fargate Spot)

**Processing Flow:**
1. Long poll SQS (20 seconds)
2. Receive message
3. Process video (frame extraction, thumbnails)
4. Update job status in DB
5. Delete message on success
6. Send to DLQ on failure (after max retries)

**Concurrency:**
- Initial: 4 concurrent jobs
- Target (10k DAU): 8-12 concurrent jobs

### AI Worker CPU

**Type:** CPU-based
**Queues:** `academy-ai-jobs-lite`, `academy-ai-jobs-basic`
**Deployment:** Docker container (EC2 or ECS Fargate Spot)

**Processing Flow:**
1. Weighted polling (Basic 3:1 Lite)
2. Receive message from selected queue
3. Enforce tier limits
4. Process job (OCR, OMR, status detection)
5. Update job status in DB
6. Delete message on success
7. Send to DLQ on failure (after max retries)

**Concurrency:**
- Initial: 2 concurrent jobs
- Target (10k DAU): 4-6 concurrent jobs

**Weighted Polling:**
- Basic queue: weight 3 (polled 3 times more frequently)
- Lite queue: weight 1
- Configurable via environment variables:
  - `AI_WORKER_BASIC_POLL_WEIGHT` (default: 3)
  - `AI_WORKER_LITE_POLL_WEIGHT` (default: 1)

### AI Worker GPU (Future)

**Type:** GPU-based
**Queue:** `academy-ai-jobs-premium`
**Deployment:** EC2 GPU instance (g4dn.xlarge)

**Processing Flow:**
1. Long poll Premium queue only
2. Process Premium tier jobs
3. GPU-accelerated OCR and analysis

## Tier Resolution

**Automatic Tier Determination:**
- Job type-based default tier assignment
- Explicit tier specification from payload supported
- Future tenant-based tier configuration extensible

**Tier Limits Enforcement:**
- Lite: OCR only
- Basic: OCR + OMR/status detection
- Premium: All job types

## Dead Letter Queue (DLQ)

**Purpose:** Isolate failed jobs for manual inspection

**Configuration:**
- Max receive count: 3 (configurable)
- After 3 failed attempts, message moves to DLQ
- Manual reprocessing possible

**DLQ Processing:**
- Monitor DLQ for messages
- Investigate failures
- Reprocess or mark as permanently failed

## Long Polling

**Configuration:**
- Wait time: 20 seconds
- Reduces empty responses
- Lowers SQS API costs
- Improves worker efficiency

## Idempotency

**Implementation:**
- Job status checked before processing
- If status is `PROCESSING` or `COMPLETED`, skip
- Prevents duplicate processing
- Handles SQS message redelivery

## Queue Creation

### Scripts

**Video Queue:**
```bash
python scripts/create_sqs_resources.py ap-northeast-2
```

**AI Queues (3-Tier):**
```bash
python scripts/create_ai_sqs_resources.py ap-northeast-2
```

### Manual Creation

**Video Queue:**
- Queue name: `academy-video-jobs`
- Visibility timeout: 300 seconds (5 minutes)
- Message retention: 14 days
- DLQ: `academy-video-jobs-dlq` (max receive count: 3)

**AI Queues:**
- Queue names: `academy-ai-jobs-lite`, `academy-ai-jobs-basic`, `academy-ai-jobs-premium`
- Visibility timeout: 300 seconds (5 minutes)
- Message retention: 14 days
- DLQ per queue (max receive count: 3)

## Environment Variables

### API Server
```env
AWS_REGION=ap-northeast-2
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

### Video Worker
```env
AWS_REGION=ap-northeast-2
VIDEO_SQS_QUEUE_NAME=academy-video-jobs
VIDEO_SQS_DLQ_NAME=academy-video-jobs-dlq
```

### AI Worker CPU
```env
AWS_REGION=ap-northeast-2
AI_WORKER_MODE=cpu
AI_SQS_QUEUE_NAME_LITE=academy-ai-jobs-lite
AI_SQS_QUEUE_NAME_BASIC=academy-ai-jobs-basic
AI_WORKER_BASIC_POLL_WEIGHT=3
AI_WORKER_LITE_POLL_WEIGHT=1
```

### AI Worker GPU (Future)
```env
AWS_REGION=ap-northeast-2
AI_WORKER_MODE=gpu
AI_SQS_QUEUE_NAME_PREMIUM=academy-ai-jobs-premium
```

## Monitoring

**Key Metrics:**
- Queue depth (messages waiting)
- DLQ depth (failed messages)
- Worker processing time
- Worker error rate
- Message age

**CloudWatch Alarms:**
- DLQ depth > 10: Alert
- Queue depth > 1000: Alert
- Worker error rate > 5%: Alert

## Cost

**Initial (500 DAU):**
- ~$2/month (SQS requests)

**Target (10k DAU):**
- ~$10/month (SQS requests)

**Cost Factors:**
- Request count (long polling reduces requests)
- Message size
- Data transfer (minimal)
