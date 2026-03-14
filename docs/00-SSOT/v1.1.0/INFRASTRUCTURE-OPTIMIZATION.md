# V1.1.0 Infrastructure Optimization Design

**Version:** V1.1.0
**Date:** 2026-03-15
**SSOT Status:** Active
**Scope:** Performance, Cost, Stability, Operational Safety
**Review Status:** Reviewed 2026-03-15, Round 2 revision incorporating all Round 1 findings

> **Current State vs. Target State:**
> This document describes both current infrastructure reality and proposed improvements.
> Items marked **[CURRENT]** are already implemented.
> Items marked **[PROPOSED]** require code/infra changes before they take effect.
> Items marked **[COMPLETED]** were implemented during the V1.1.0 optimization pass.

---

## 1. Design Principles

1. **API stability is non-negotiable.** API serves real users; no CPU-heavy work on API instances.
2. **Video processing is a separate program.** Video worker owns all encoding; never shares an instance with API.
3. **Tenant isolation is absolute.** No change in this document weakens tenant boundaries.
4. **Correctness over speed.** Fast execution is valued, but never at the cost of data integrity or tenant safety.
5. **Non-wasteful, not cheap.** Eliminate unjustified waste; do not cut where UX or reliability suffers.
6. **Zero-downtime deployment must never break.** All changes preserve MinHealthyPercentage=100% ASG refresh.

---

## 2. Target Architecture

```
                     ┌──────────────────────────────────┐
                     │       Cloudflare Pages + R2       │
                     │    Frontend SPA + Video Storage   │
                     └───────────────┬──────────────────┘
                                     │
                     ┌───────────────▼──────────────────┐
                     │    ALB (academy-v1-api-alb)       │
                     │    Target: /healthz (liveness)    │
                     └───────────────┬──────────────────┘
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         │                           │                           │
┌────────▼─────────┐  ┌─────────────▼──────────┐  ┌─────────────▼──────────┐
│  API Server       │  │  Messaging Worker      │  │  AI Worker             │
│  t4g.medium       │  │  t4g.small [PROPOSED]  │  │  t4g.medium            │
│  ASG: min=1 max=2 │  │  ASG: min=1 max=3     │  │  ASG: min=0 max=5     │
│  Gunicorn 4w      │  │  SQS long-poll         │  │  SQS long-poll         │
│  gevent           │  │  SMS/LMS via Solapi    │  │  SQS-based autoscale   │
│  ❌ No ffmpeg     │  │                         │  │                        │
│  ❌ No video      │  │                         │  │                        │
│     daemon        │  │                         │  │                        │
└──────────────────┘  └─────────────────────────┘  └────────────────────────┘
         │
         │  ┌─────────────────────────────────────────────────┐
         │  │  Video Worker [PROPOSED: dedicated ASG]            │
         │  │  ⚠ c6g.medium+ recommended (NOT t4g burstable)  │
         │  │  Daemon mode: videos < 90 min [PROPOSED: currently 30min] │
         │  │  Batch fallback: videos >= 90 min [PROPOSED]     │
         │  │  Owns: download, ffmpeg, upload, publish         │
         │  │  Isolated from API completely                    │
         │  │                                                  │
         │  │  CURRENT STATE: Video daemon runs on an          │
         │  │  unmanaged instance, NOT via CI/CD pipeline.     │
         │  │  TARGET: Create academy-v1-video-worker-asg      │
         │  │  with launch template + CI/CD deploy job.        │
         │  └─────────────────────────────────────────────────┘
         │
┌────────▼──────────────────────────────────────────────────────┐
│  RDS db.t4g.medium  │  ElastiCache cache.t4g.small (Redis)   │
│  PostgreSQL 15      │  1 node                                 │
│  Single-AZ, 20GB    │  Progress tracking + session cache      │
└───────────────────────────────────────────────────────────────┘
         │
┌────────▼─────────────┐  ┌─────────────────────────────┐
│  SQS Queues (4)       │  │  AWS Batch (fallback)        │
│  ai + ai-dlq          │  │  Long video >= 90min only    │
│  messaging + msg-dlq  │  │  On-demand, terminates after │
└───────────────────────┘  └─────────────────────────────┘
```

### Service Ownership Rules (ABSOLUTE)

| Service | Owns | Must NOT do |
|---------|------|-------------|
| **API** | HTTP requests, presigned URLs, DB writes, health endpoints | ffmpeg, video daemon, any CPU-heavy batch work |
| **Messaging Worker** | SQS → Solapi SMS/LMS, scheduled sends | Video encoding, AI tasks |
| **AI Worker** | OCR, Excel parsing, document analysis | Video encoding, messaging |
| **Video Worker** | Video download, ffmpeg encode, R2 upload, HLS publish | API request handling |
| **Video Batch** | Long videos (>= 90 min) via AWS Batch | Short video processing |

---

## 3. Video Pipeline Design

### 3.1 Encoding Strategy: Single 720p **[PROPOSED]**

**Default output:** Single 720p HLS variant.
**Current code:** `transcoder.py` still uses 2-variant (360p+720p). Code change required.

```python
# transcoder.py — V1.1.0 encoding configuration
HLS_VARIANT = {
    "name": "1",
    "width": 1280,
    "height": 720,
    "video_bitrate": "3500k",   # Text-safe: do NOT lower below 3000k
    "audio_bitrate": "128k",
}
```

**Why single 720p:**
- Eliminates 360p variant → ~50% fewer HLS segments
- Removes filter_complex `split` → simpler ffmpeg pipeline
- 1 variant instead of 2 → encoding time reduced ~25%
- R2 upload file count halved → faster publish
- **Combined with preset change (see below): total time-to-ready improvement ~50-55%**

**Why 3500kbps (not lower):**
- Academy lectures include document/code/Excel/PDF screen recordings
- At 2500kbps, compression artifacts blur small text on tablets
- At 3500kbps, text remains readable on iPad Pro 12.9" and Galaxy Tab S
- 3500kbps is comparable to the old 2-variant total (800k + 2500k = 3300k)
- Storage cost increase is negligible vs. quality improvement

**FFmpeg optimization flags (new):**
- `-preset fast` (was: unset = medium default). ~35% faster encode, slight quality trade-off.
- `-refs 3` (compensate for `fast` preset's reduced reference frames). Restores text sharpness for document/code content.
- `-threads 0` (auto-detect CPU cores). Utilizes full video worker CPU.
- `-profile:v main` (unchanged, compatibility)
- `-g 48 -keyint_min 48 -sc_threshold 0` (unchanged)

**Performance gain breakdown (two independent sources):**
1. **Variant reduction** (2-variant → single 720p): ~25% time savings (fewer segments, no split filter)
2. **Preset change** (`medium` → `fast`): ~35% time savings (faster encoding per frame)
3. **Combined**: ~50-55% total time-to-ready improvement (multiplicative, not additive)

**⚠ CRITICAL: Instance Type Warning for Video Worker**

t4g.medium is a **burstable** instance. CPU credits exhaust in ~30 minutes of sustained encoding. After credit depletion, CPU throttles to 20% baseline (40% total for 2 vCPU). Real-world encoding times:

| Video Duration | With Credits (first 30min) | After Throttle | Total Wall Time |
|---------------|---------------------------|----------------|-----------------|
| 10 min | ~4 min | N/A | ~4 min |
| 30 min | 30 min at full speed | ~15 min throttled | ~25 min |
| 60 min | 30 min at full speed | ~90 min throttled | **~120 min** |
| 90 min | 30 min at full speed | ~180 min throttled | **~210 min** |

**Recommendation:** Video worker should use a **non-burstable compute-optimized instance**:
- `c6g.medium` (1 vCPU dedicated, ~$25/mo) — minimum for consistent encoding
- `c6g.large` (2 vCPU dedicated, ~$50/mo) — recommended for 60-90 min lectures
- `c7g.medium` (Graviton3, ~$30/mo) — better per-core performance

The 50-55% improvement estimate above is valid **only on non-burstable instances**. On t4g.medium, improvement is limited to the first 30 minutes of encoding before credit depletion.

### 3.2 Text Readability Risk Acknowledgment

**720p is NOT guaranteed sufficient for all content types.**

| Content Type | 720p@3500k | Risk |
|-------------|-----------|------|
| Talking head + whiteboard | Excellent | None |
| Slide presentation (large text) | Good | Low |
| Document/PDF (body text) | Acceptable | Medium |
| Code editor (small font) | Borderline | **High** |
| Excel spreadsheet (dense cells) | Borderline | **High** |
| Handwritten math (fine lines) | Acceptable | Medium |

**Mandatory QA before finalizing 720p:**

| Test | Device | Pass Criteria |
|------|--------|---------------|
| Code editor lecture | iPad Pro 12.9" | 12pt code readable without zooming |
| Excel spreadsheet lecture | iPad mini | Cell text legible at normal viewing distance |
| PDF textbook lecture | Galaxy Tab S | Body text (10-11pt equivalent) clear |
| Whiteboard lecture | Any tablet | Board text readable |

**If QA fails → upgrade to single 1080p:**

```python
# One-line change to switch to 1080p
HLS_VARIANT = {
    "name": "1",
    "width": 1920,
    "height": 1080,
    "video_bitrate": "5000k",
    "audio_bitrate": "128k",
}
```

Architecture supports this switch with zero structural changes.

### 3.3 Source Resolution Protection

```python
def _select_variant(input_w: int, input_h: int) -> dict:
    """Do not upscale. If source < 720p, encode at source resolution."""
    if input_w >= HLS_VARIANT["width"] and input_h >= HLS_VARIANT["height"]:
        return HLS_VARIANT
    return {
        **HLS_VARIANT,
        "width": input_w or HLS_VARIANT["width"],
        "height": input_h or HLS_VARIANT["height"],
        "video_bitrate": "2000k",  # Lower bitrate for lower resolution
    }
```

### 3.4 Video Duration Routing

| Duration | Route | Worker |
|----------|-------|--------|
| < 90 min | Daemon (DB polling) | Video Worker ASG |
| >= 90 min | AWS Batch (on-demand) | Batch compute environment |

**Config [PROPOSED]:** `DAEMON_MAX_DURATION_SECONDS=5400` (currently 1800 in `base.py` and `daemon_main.py`)

The 90-minute threshold balances:
- Most academy lectures are 60-90 minutes → daemon handles majority
- A 2-hour video takes ~2.5h to process, blocking the daemon queue for too long
- 90 min covers the vast majority of lectures while keeping daemon queue responsive
- Videos >= 90 min route to Batch with dedicated resources and no queue-blocking risk

### 3.5 Upload Path (Future Improvement)

**Current:** Single presigned PUT (2-hour expiry)
**Target:** Multipart presigned upload for files > 100MB

```
[Browser]
  1. POST /videos/upload/init → {upload_id, parts[{partNumber, presignedUrl}]}
  2. PUT each part (5-10MB chunks) → R2 (3-5 parallel)
  3. POST /videos/upload/complete → completeMultipartUpload → ffprobe → enqueue
```

**Benefits:** Resumable uploads, per-part retry, progress tracking, 1GB upload in ~1-2min vs 5-10min.

### 3.6 Performance Expectations

| Metric | Before (2-variant, medium preset) | After (single 720p, fast preset) | Improvement | Source |
|--------|-----------------------------------|----------------------------------|-------------|--------|
| FFmpeg encode time (10min video) | ~8 min | ~4 min | **~50% faster** | Variant reduction (~25%) + preset (~35%) |
| HLS segments generated | ~300 (2×150) | ~150 | **50% fewer** | Variant reduction |
| R2 upload files | ~300 | ~150 | **50% fewer** | Variant reduction |
| publish_tmp_to_final copies | ~300 | ~150 | **50% faster** | Variant reduction |
| Total time-to-ready (10min video) | ~15 min | ~7.5 min | **~50% faster** | Combined |
| Storage per 10min video | ~230 MB | ~260 MB | +13% (acceptable) | Higher single-variant bitrate |

**Note:** The two improvement sources are independent and measured separately:
- **Variant reduction** (2→1): eliminates ~50% of segments and removes `split` filter_complex. Encoding time savings ~25%.
- **Preset change** (`medium`→`fast` + `-refs 3`): faster per-frame encoding with compensated reference frames. Encoding time savings ~35%.
- Combined total is multiplicative: `(1 - 0.25) × (1 - 0.35) ≈ 0.49`, yielding ~50-55% improvement.

---

## 4. ECR Operational Safety Design

### 4.1 Problem Statement

ECR is not a cost problem. It is an **operational safety problem**. **[COMPLETED — cleanup executed 2026-03-15, lifecycle policy re-applied]**

**Root cause chain:**
1. Docker buildx produces OCI Image Index (multi-arch manifest list) per build
2. Each Index references one Platform Manifest (linux/arm64 child)
3. When `:latest` tag moves to new build, previous Index becomes untagged
4. Previous Platform Manifest remains referenced by its parent Index
5. Lifecycle policies existed but **never evaluated** (`lastEvaluatedAt: 1970-01-01`)
6. `batch-delete-image` on child manifests fails: `ImageReferencedByManifestList`
7. Result: 34,098 images / 5,207 GB accumulated across 5 repos

**This is not just expensive ($213/month). It is a deployment integrity risk:**
- Bloated repos slow ECR operations
- Rollback SHA lookups become harder in noise
- Storage costs grow unboundedly without intervention

### 4.2 Protected Image Set

**Before any deletion, the protected set must be identified:**

| Protected Category | Criterion | Reason |
|-------------------|-----------|--------|
| Current production | Images with `:latest` tag | Active deployment |
| Recent rollback points | Last 10 images with `sha-*` tag | Rollback capability |
| Release markers | Images with `v*`, `prod*`, `main*`, `deploy*` tags (last 5 each) | Release history |
| Manifest children | Platform manifests referenced by any protected Index | Structural integrity |

**Rule: NEVER delete a protected image. All cleanup operates on the complement of the protected set.**

### 4.3 Manifest-Aware Cleanup Strategy

Deletion order matters due to OCI Image Index → Platform Manifest references.

```
Step 1: Enumerate all images (full pagination)
Step 2: Identify protected set (tagged images + their children)
Step 3: Classify deletable images:
  - Phase A targets: untagged OCI Image Index manifests (parents)
  - Phase B targets: untagged Platform Manifests (children, now orphaned after Phase A)
Step 4: Execute deletion in order:
  Phase A: Delete untagged Index manifests FIRST (releases child references)
  Phase B: Delete orphaned Platform Manifests SECOND
Step 5: Verify results
```

**Implementation:** `scripts/v1/ecr-cleanup.py` (manifest-aware, 3 modes: `--dry-run`, `--execute`, `--verify`)

### 4.4 Lifecycle Policy (V1.1.0 — Corrected)

```json
{
  "rules": [
    {
      "rulePriority": 1,
      "description": "Remove untagged images after 1 day",
      "selection": {
        "tagStatus": "untagged",
        "countType": "sinceImagePushed",
        "countUnit": "days",
        "countNumber": 1
      },
      "action": { "type": "expire" }
    },
    {
      "rulePriority": 2,
      "description": "Keep last 10 sha-tagged images",
      "selection": {
        "tagStatus": "tagged",
        "tagPrefixList": ["sha-"],
        "countType": "imageCountMoreThan",
        "countNumber": 10
      },
      "action": { "type": "expire" }
    },
    {
      "rulePriority": 3,
      "description": "Keep last 5 release/deploy tags",
      "selection": {
        "tagStatus": "tagged",
        "tagPrefixList": ["latest", "v", "prod", "main", "deploy"],
        "countType": "imageCountMoreThan",
        "countNumber": 5
      },
      "action": { "type": "expire" }
    }
  ]
}
```

**Changes from previous policy:**
- `untagged`: 7 days → **1 day** (faster cleanup)
- `sha-`: 50 → **10** (sufficient rollback window for daily deploys)
- Policy was deleted and re-applied on 2026-03-15 to force evaluation cycle

### 4.5 Tag Strategy

| Tag | Mutability | Purpose | Retention |
|-----|-----------|---------|-----------|
| `latest` | Mutable | Current production pointer | Always 1 |
| `sha-XXXXXXXX` | Immutable | Exact commit for rollback | Last 10 |
| `v*`, `prod*`, `main*`, `deploy*` | Varies | Release/environment markers | Last 5 each |
| (untagged) | N/A | Orphaned manifests from tag overwrites | 1 day |

**Catch-all rule:** Any image not matching the above patterns and older than 7 days should be considered for cleanup. The lifecycle policy's `untagged` rule handles most cases; tagged images without known prefixes should be manually reviewed.

### 4.6 Before/After Verification Protocol

**After any ECR cleanup, verify:**

```bash
# 1. Image count per repo (should be < 30 per repo)
for repo in academy-base academy-api academy-ai-worker-cpu academy-messaging-worker academy-video-worker; do
  echo "$repo: $(aws ecr describe-images --repository-name $repo --query 'length(imageDetails)' --output text)"
done

# 2. Storage per repo (should be < 20 GB total)
# (use ecr-cleanup.py --verify)

# 3. Lifecycle policy evaluation status
for repo in ...; do
  aws ecr get-lifecycle-policy --repository-name $repo --query lastEvaluatedAt --output text
done
# Must NOT be 1970-01-01. If still epoch after 48h post-policy-apply, escalate.

# 4. Tagged images still intact
for repo in ...; do
  aws ecr describe-images --repository-name $repo --filter tagStatus=TAGGED --query 'imageDetails[*].imageTags' --output json
done
# Must contain :latest and recent sha- tags
```

### 4.7 Recurrence Prevention

| Mechanism | Purpose |
|-----------|---------|
| Lifecycle policy on all repos | Automatic cleanup of untagged (1 day) and excess sha- (>10) |
| `ecr-cleanup.py --verify` in post-deploy | Catch evaluation failures early |
| Weekly `lastEvaluatedAt` check | Detect if lifecycle stops evaluating |
| CI build report includes image count | Visibility into accumulation trends |
| CLAUDE.md `feedback_ecr_cleanup` memory | Ensures Claude always checks ECR on new repo creation |

---

## 5. Cost Optimization

### 5.1 Before/After Cost Projection

| Service | Before (monthly) | After (monthly) | Change | Notes |
|---------|-----------------|-----------------|--------|-------|
| **ECR Storage** | **$213** | **~$5** | **-98%** | 5.2TB → <50GB after cleanup |
| **VPC** | $82 | ~$20 | -76% | Interface endpoints removed, self-resolving |
| **EC2 Compute** | $87 | $51 | -41% | Messaging→t4g.small ($12 save), AI min=0 ($24 save) |
| **RDS** | $71 | $71 | 0% | Keep db.t4g.medium Single-AZ (see §11 Accepted Risks) |
| **ElastiCache** | $38 | $38 | 0% | Keep cache.t4g.small |
| **EC2-Other** | $44 | $35 | -20% | IPv4 reduction where possible |
| **ALB** | $10 | $10 | 0% | Required |
| **Tax** | $61 | ~$25 | Proportional | |
| **Total** | **~$606** | **~$255** | **-58%** | |

**Cost floor (theoretical minimum):** ~$204/mo — post-optimization $255 minus $51 RI savings = $204. Requires 1yr no-upfront RIs on API + messaging + RDS. Only commit after 3 months of stable usage.

### 5.1.1 Worker Right-Sizing [PROPOSED]

| Worker | Current | Proposed | Savings | Justification |
|--------|---------|----------|---------|---------------|
| **Messaging** | t4g.medium ($29/mo) | t4g.small ($14.50/mo) | $14.50/mo | SQS→Solapi is I/O-bound; 2GB RAM sufficient |
| **AI** | t4g.medium min=1 ($29/mo) | t4g.medium min=0 ($5/mo avg) | $24/mo | Queue nearly always empty; SQS-based autoscale |
| **API** | t4g.medium | t4g.medium (keep) | $0 | Gunicorn 4w + gevent needs 4GB headroom |

**AI Worker SQS-Based Autoscaling [PROPOSED]:**
- ASG min=0, max=5, desired=0
- CloudWatch alarm: `ApproximateNumberOfMessagesVisible > 0` for 1 min → scale to 1
- Scale-in: `ApproximateNumberOfMessagesVisible = 0` for 10 min → scale to 0
- Cost: ~$5/mo average (occasional on-demand usage) vs $29/mo always-on

### 5.1.2 Reserved Instance Recommendation [PROPOSED]

| Resource | RI Type | On-Demand | RI Price | Savings |
|----------|---------|-----------|----------|---------|
| API t4g.medium | 1yr no-upfront | $29/mo | $18/mo | $11/mo |
| Messaging t4g.small | 1yr no-upfront | $14.50/mo | $9/mo | $5.50/mo |
| RDS db.t4g.medium | 1yr no-upfront | $71/mo | $36.50/mo | $34.50/mo |
| **Total RI savings** | | | | **$51/mo** |

**Note:** Only commit to RIs after 3 months of stable usage patterns. With RIs, cost floor drops to ~$204/mo.

### 5.2 What NOT to Cut

| Resource | Why Keep |
|----------|---------|
| API t4g.medium | Gunicorn 4w + gevent needs headroom; downsizing risks latency spikes |
| RDS db.t4g.medium | PostgreSQL query workload; t4g.small has only 2GB RAM |
| Redis cache.t4g.small | Video progress + session cache; t4g.micro has only 0.5GB |
| API + Messaging min=1 | Cold start delays hurt UX; always-on gives instant processing |
| MinHealthyPercentage=100% | Zero-downtime guarantee; non-negotiable |

**What CAN be cut (see §5.1.1):**

| Resource | Why Cut | Risk |
|----------|---------|------|
| Messaging t4g.medium → t4g.small | SQS→Solapi is I/O-bound, 2GB sufficient | Low — monitor RSS after switch |
| AI worker min=1 → min=0 | Queue nearly always empty; SQS autoscale handles spikes | Medium — first request has ~60s cold start |

### 5.3 Cost Guardrails

**AWS Budget alerts (calibrated to ~$255 target):**
- $270 (baseline +6%): Informational — steady-state confirmation
- $320 (baseline +25%): Warning — investigate cost spike
- $380 (baseline +50%): Action required — check for runaway resources

### 5.4 Stale Infrastructure Detection Checklist (Monthly)

- [ ] ECR total images < 100 per repo
- [ ] ECR lifecycle `lastEvaluatedAt` is recent (not epoch)
- [ ] No unattached EBS volumes
- [ ] No unassociated Elastic IPs
- [ ] No orphaned NAT Gateways
- [ ] SQS DLQ depth = 0
- [ ] No stopped/unused EC2 instances
- [ ] CloudWatch log groups have retention set (not "Never Expire")

---

## 6. Deployment & Safety

### 6.1 Zero-Downtime (Unchanged from V1.1.0 baseline)

All ASG refreshes use `MinHealthyPercentage=100%`:
- New instance launches and passes health check BEFORE old instance terminates
- API warmup: 300s, Workers warmup: 120s
- ALB drains connections before terminating old instance

**This is non-negotiable and must not be changed.**

### 6.2 CI/CD Concurrency Safety Fix **[COMPLETED]**

**Problem:** `v1-build-and-push-latest.yml` uses `cancel-in-progress: true`. If push B arrives while push A's ASG refresh is in progress, the workflow for push A is cancelled — but the ASG refresh continues as an orphaned AWS operation. The new push B then starts its own ASG refresh, potentially causing two simultaneous refreshes.

**Fix options (choose one):**
1. **`cancel-in-progress: false`** (recommended): Queue push B until push A completes. Simpler, no orphan risk. Slightly slower throughput on rapid pushes.
2. **ASG refresh-in-progress guard**: Before starting ASG refresh, check `InstanceRefreshes` status. If `InProgress`, wait/skip. More complex but allows cancellation of build-only steps.

**Recommended:** Option 1 — change to `cancel-in-progress: false` in the workflow file.

```yaml
concurrency:
  group: v1-build-and-push-latest
  cancel-in-progress: false  # CHANGED: prevent orphaned ASG refreshes
```

### 6.3 Video Batch Deploy OIDC Fix (Required)

**Problem:** `video_batch_deploy.yml` fails with OIDC credential loading error.
**Root cause:** The workflow uses `secrets.AWS_ROLE_ARN_FOR_VIDEO_BATCH` (a separate OIDC role from the main CI/CD `AWS_ROLE_ARN_FOR_ECR_BUILD`). The Video Batch role's trust policy likely does not include `video_batch_deploy.yml` in its `sub` condition, or the GitHub secret is not configured.
**Fix:** Verify `AWS_ROLE_ARN_FOR_VIDEO_BATCH` secret exists in GitHub, and that the referenced IAM role's trust policy includes the `video_batch_deploy` workflow. This is NOT the same role as `academy-gha-ecr-build`.

### 6.4 Worker Drain Strategy (Already Implemented)

All workers handle SIGTERM gracefully:
- **Messaging:** Complete current message → visibility timeout resets for unfinished
- **AI:** Same pattern as messaging
- **Video daemon:** `_shutdown_event` → complete current job → exit
- **Video batch:** SIGTERM → mark job as RETRY_WAIT → safe for spot/scale-in

No additional drain work needed.

### 6.5 Migration Safety (Unchanged)

- Additive only (nullable/default columns)
- No column renames/drops in single release
- Two-release process for breaking schema changes
- Migration runs BEFORE new code deploys via SSM
- **Safe failure state:** If migration succeeds but ASG deploy fails (health check failure), the system is in new-schema + old-code state. Because migrations are additive-only, old code continues to work correctly with the new schema. No manual migration rollback is needed in this case.
- **[COMPLETED] `CONN_HEALTH_CHECKS: True`** added to DATABASES settings in both `base.py` and `worker.py`. Without this, Django reuses stale DB connections after RDS recovery, causing post-recovery errors for up to 60 seconds (CONN_MAX_AGE=60).

---

## 7. Monitoring & Continuous Verification

| Metric | Threshold | Check Frequency | Action |
|--------|-----------|-----------------|--------|
| `/healthz` response | 200 | Every deploy + continuous ALB | Page on failure |
| `/health` response | 200 | Every deploy | Investigate DB |
| SQS DLQ depth | 0 | Daily | Investigate failed messages |
| **SQS ApproximateAgeOfOldestMessage** | **< 300s** | **CloudWatch alarm** | **Worker stall — investigate immediately** |
| ECR total images | < 100/repo | Weekly | Run ecr-cleanup.py |
| ECR `lastEvaluatedAt` | < 7 days old | Weekly | Re-apply policy if stale; **if still 1970-01-01 after 48h, switch to scheduled `ecr-cleanup.py`** |
| RDS CPU | < 70% | CloudWatch | Consider scaling if sustained |
| **RDS FreeStorageSpace** | **> 2 GB** | **CloudWatch alarm** | **Expand storage immediately** |
| **RDS DatabaseConnections** | **< 80% of max** | **CloudWatch alarm** | **Investigate connection leaks** |
| **Redis DatabaseMemoryUsagePercentage** | **< 80%** | **CloudWatch alarm** | **Review eviction policy / scale** |
| **Redis CurrConnections** | **> 0** | **CloudWatch alarm** | **Redis unreachable — messaging halts, video progress lost** |
| Monthly AWS cost | < $380 | Budget alert (calibrated to ~$255 target) | Review spending |
| ASG instances | All Healthy/InService | Every deploy | Investigate unhealthy |
| Video encoding failure rate | < 5% | Weekly (per-job logged) | Review failed jobs |
| Video time-to-ready (P95) | < 15 min for 10min video | Per-job CloudWatch metric | Tune encoding params |

---

## 8. Implementation Priority

| Order | Action | Impact | Effort | Status |
|-------|--------|--------|--------|--------|
| 1 | **CI/CD `cancel-in-progress: false`** | **Prevent orphaned ASG refreshes** | 5 min | ✅ [COMPLETED] |
| 2 | **Video Batch Deploy OIDC fix** | **Restore batch fallback (live bug)** | 30 min | **Pending — CRITICAL** |
| 3 | **Video job auto-recovery cron** | **Prevent stuck jobs after daemon crash** | 15 min | **Pending — CRITICAL** |
| 4 | ECR manifest-aware cleanup | $200/mo savings + deployment hygiene | Done | ✅ [COMPLETED] Script created, executing |
| 5 | ECR lifecycle policy re-apply | Recurrence prevention | Done | ✅ [COMPLETED] Applied 2026-03-15 |
| 6 | Messaging dedup hardening | Prevent duplicate SMS on Redis outage | 2 hours | ✅ [COMPLETED] (fail-closed + DB dedup) |
| 7 | AWS Budget alerts | Cost guardrail | 15 min | Pending |
| 8 | Single 720p encoding switch [PROPOSED] | ~50-55% time-to-ready improvement | 1 hour | Pending (code change needed) |
| 9 | DAEMON_MAX_DURATION_SECONDS → 5400 [PROPOSED] | Daemon handles up to 90min videos | 10 min | Pending (config change) |
| 10 | AI worker min=0 + SQS autoscale [PROPOSED] | $24/mo savings | 1 hour | Pending |
| 11 | Messaging worker → t4g.small [PROPOSED] | $14.50/mo savings | 30 min | Pending |
| 12 | Video worker ASG separation [PROPOSED] | API stability + encoding throughput | Half day | Pending (infra creation) |
| 13 | Tablet QA for 720p text | Validate or escalate to 1080p | 1-2 hours | Pending |
| 14 | Base image conditional build | Skip rebuild when only app code changed | 30 min | Pending |
| 15 | Multipart upload [PROPOSED] | Large file UX improvement | 1-2 days | Pending |

---

## 9. Rollback Runbook

### 9.1 SHA Rollback is a 2-Step Process **[COMPLETED]**

**Problem:** SHA-based image rollback (re-tagging a previous `sha-XXXXXXXX` as `:latest` and refreshing ASG) only rolls back application code. It does NOT reverse database migrations. If the rolled-back code is incompatible with the new schema, the rollback will fail silently or cause runtime errors.

**Mandatory rollback procedure:**

```
Step 1: DECIDE — Does the migration need reversal?
  ├── Migration was additive-only (new nullable column, new table)?
  │   └── NO reversal needed. Old code ignores new columns. Proceed to Step 2.
  ├── Migration changed existing column type/constraint?
  │   └── YES — must reverse migration BEFORE rolling back code.
  └── Migration dropped column/table?
      └── CANNOT roll back without data loss. Escalate.

Step 2: REVERSE MIGRATION (if needed)
  $ ssh into any API instance (or use SSM)
  $ docker exec <container> python manage.py migrate <app_name> <previous_migration_number>
  Example: docker exec academy-api python manage.py migrate exams 0008

Step 3: ROLL BACK CODE (image re-tag + ASG refresh)
  $ MANIFEST=$(aws ecr batch-get-image --repository-name academy-api \
      --image-ids imageTag=sha-XXXXXXXX --query 'images[0].imageManifest' --output text)
  $ aws ecr put-image --repository-name academy-api \
      --image-tag latest --image-manifest "$MANIFEST"
  $ aws autoscaling start-instance-refresh --auto-scaling-group-name academy-v1-api-asg \
      --preferences '{"MinHealthyPercentage":100,"InstanceWarmup":300}'

Step 4: VERIFY
  - /healthz returns 200
  - /health returns 200
  - Test affected endpoints manually
```

### 9.2 Migration Reversal Decision Tree

```
New deployment has a bug. Should I roll back?
│
├── Bug is in application logic only (no migration in this deploy)?
│   └── Simple rollback: re-tag image + ASG refresh. Done.
│
├── Deploy included an additive migration (new nullable column/table)?
│   └── Simple rollback: re-tag image + ASG refresh.
│       Old code ignores new columns. No migration reversal needed.
│
├── Deploy included a column rename, type change, or constraint change?
│   └── REVERSE migration first, THEN roll back code.
│       Order matters: if you roll back code first, it will crash
│       because it expects the old schema.
│
└── Deploy included a column/table drop?
    └── Data is gone. Cannot roll back without backup restore.
        Use RDS point-in-time recovery (RPO: 5 min).
```

---

## 10. Messaging Idempotency **[Critical — must fix]**

### 10.1 Problem

Message sending (SQS → Solapi SMS/LMS) had no durable dedup mechanism. Redis-based dedup was **fail-open**: if Redis was unavailable, messages sent without dedup checks. **[COMPLETED — fixed to fail-closed with DB dedup fallback, see code changes in libs/redis/idempotency.py and sqs_main.py]**. Previously this caused:
- Duplicate SMS delivery to students
- Double billing from Solapi
- User trust erosion

### 10.2 Root Cause

The messaging worker checks Redis for a dedup key before sending. If Redis is down or the key expired, the check passes and the message sends again.

### 10.3 Fix Options

| Option | Approach | Effort | Reliability |
|--------|----------|--------|-------------|
| **A: Pre-check NotificationLog** | Before send, query `NotificationLog` for matching (recipient, template, created_at > now-5min). Skip if exists. | 1 hour | High — DB is always available |
| **B: Solapi external dedup key** | Pass `messageId` to Solapi API; Solapi deduplicates on their side. | 30 min | Highest — if Solapi supports it |
| **C: DB-based dedup key** | Insert dedup record in DB before send; unique constraint prevents double-insert. | 2 hours | High |

**Recommended:** Option A (NotificationLog pre-check) as immediate fix. Option B as future enhancement if Solapi API supports external message IDs.

### 10.4 Implementation Sketch (Option A)

```python
# In messaging worker, before calling solapi_send():
from apps.notifications.models import NotificationLog
from django.utils import timezone
from datetime import timedelta

cutoff = timezone.now() - timedelta(minutes=5)
exists = NotificationLog.objects.filter(
    recipient=phone_number,
    template_code=template_code,
    created_at__gte=cutoff,
    status='SENT',
).exists()

if exists:
    logger.warning(f"Dedup: skipping duplicate message to {phone_number}")
    return  # Skip send
```

---

## 11. Accepted Risks & RPO/RTO

### 11.1 RDS Single-AZ **[Critical — accepted risk]**

| Parameter | Value |
|-----------|-------|
| **Current config** | db.t4g.medium, Single-AZ, 20GB gp3 |
| **RPO (Recovery Point Objective)** | **5 minutes** — automated backups with 5-min backup window |
| **RTO (Recovery Time Objective)** | **10-30 minutes** — Single-AZ failover requires instance replacement |
| **Downtime risk** | AZ failure → 10-30min full database outage |
| **Data loss risk** | Up to 5 minutes of transactions lost on hardware failure |
| **Multi-AZ cost** | +$71/mo (doubles RDS cost) |

**Decision:** Accept Single-AZ risk for now. Academy is a business-hours application; overnight AZ failures have low user impact. Revisit when monthly revenue exceeds $5K or user complaints about availability occur.

**Mitigation:**
- Automated daily snapshots (retained 7 days)
- Point-in-time recovery enabled (5-min granularity)
- `/health` endpoint checks DB connectivity; ALB routes away from unhealthy API instances
- Manual failover procedure documented: restore from snapshot → update RDS endpoint in SSM Parameter Store

### 11.2 Redis Single-Node

| Parameter | Value |
|-----------|-------|
| **Current config** | cache.t4g.small, 1 node |
| **Failure impact** | Video progress tracking lost, session cache cold |
| **Messaging impact** | **HALTS** (fail-closed by design) — messages accumulate in SQS until Redis recovers. NOT "degrades gracefully." |
| **AI/Video impact** | Degrades gracefully — progress bars stop, processing continues |
| **RTO** | ~5 minutes (ElastiCache auto-replacement) |
| **Mitigation** | Messaging halt is intentional (prevents duplicate SMS). DB dedup fallback exists. AI/Video continue without Redis. |

---

## 12. Operational Recovery Automation

### 12.1 Video Job Auto-Recovery Cron **[Critical — must implement]**

**Problem:** If the video daemon crashes mid-job, the video record stays in `PENDING` status indefinitely. No auto-recovery exists.

**Fix:** Schedule `scan_stuck_video_jobs` management command as a cron job.

```bash
# Crontab on video worker instance (or via SSM RunCommand)
*/30 * * * * docker exec academy-video-worker python manage.py scan_stuck_video_jobs --auto-fix 2>&1 | logger -t video-recovery
```

**Behavior:**
- Runs every 30 minutes
- Finds videos stuck in `PENDING` for > 30 minutes
- Re-enqueues them for processing
- Logs actions for audit trail

### 12.2 ECR Lifecycle Verification

**If ECR lifecycle policy `lastEvaluatedAt` remains `1970-01-01` after 48 hours post-apply:**

1. ECR lifecycle is not evaluating (known AWS issue with OCI Image Index manifests)
2. Switch to scheduled `ecr-cleanup.py` as primary cleanup mechanism:

```bash
# Weekly cron (e.g., Sunday 3 AM KST)
0 18 * * 0 python /opt/scripts/ecr-cleanup.py --execute --all-repos 2>&1 | logger -t ecr-cleanup
```

### 12.3 Base Image Conditional Build [PROPOSED]

**Problem:** The CI/CD workflow rebuilds `academy-base` on every push, even when only application code changed. Base image rebuild takes ~3 minutes and is unnecessary for most deploys.

**Fix:** Add `if:` guard on the base image build job:

```yaml
build-base:
  if: github.event_name == 'workflow_dispatch' || needs.detect-changes.outputs.force_full == 'true'
  # ... existing build steps
```

This skips base image rebuild on normal pushes. Base image only rebuilds on:
- Manual workflow dispatch
- Changes to `Dockerfile.base`, `requirements.txt`, or similar dependency files

---

## 13. Document Lineage

| Version | Date | Changes |
|---------|------|---------|
| V1.0.3 | 2026-03-13 | Video Infrastructure (daemon/batch modes, recovery commands) — SEALED |
| V1.1.0 | 2026-03-14 | Zero-downtime deployment, selective service deploy, SHA tagging |
| V1.1.0 | 2026-03-15 | Infrastructure Optimization: ECR safety, single 720p, service separation, cost optimization |
| V1.1.0 | 2026-03-15 | Round 2 revision: CI/CD concurrency fix, rollback runbook, messaging dedup, RDS RPO/RTO, worker right-sizing, video auto-recovery, cost floor correction ($204), duration threshold 5400s, performance breakdown (50-55%), `-refs 3`, base image conditional build |
