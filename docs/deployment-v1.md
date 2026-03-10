# V1 Deployment — Video Encoding Pipeline

## Architecture

```
API Server (ECS ASG — academy-v1-api-asg)
  ↓  POST /api/v1/media/videos/{id}/upload/complete/
  ↓  batch.submit_job() → VIDEO_BATCH_JOB_QUEUE
  ↓
AWS Batch Job Queue (academy-v1-video-batch-queue)
  ↓
Compute Environment (academy-v1-video-batch-ce)
  EC2 c6g.xlarge, ARM64, ECS_AL2023 AMI, Spot
  Launch Template: academy-v1-video-batch-lt (root=200GB, gp3)
  ↓
Container: academy-video-worker (ECR, linux/arm64)
  batch_entrypoint.py → SSM /academy/workers/env → batch_main.py
  ↓ 7-step pipeline
  1. presign R2 GET URL
  2. download source.mp4
  3. ffprobe duration
  4. ffmpeg → HLS (360p + 720p, master.m3u8)
  5. validate HLS output
  6. thumbnail.jpg
  7. upload to R2 tmp → publish final → verify integrity
  ↓
Video.status = READY, hls_path saved

Long-video variant (>= 3h):
  Queue: academy-v1-video-batch-long-queue
  CE: academy-v1-video-batch-long-ce
  EC2 On-Demand (not Spot), root=300GB
```

### Supporting Infrastructure

| Resource | Name | Purpose |
|---|---|---|
| DynamoDB | academy-v1-video-job-lock | 1-video-1-job distributed lock |
| DynamoDB | academy-v1-video-upload-checkpoints | R2 multipart upload resumption |
| EventBridge | academy-v1-reconcile-video-jobs | 1h — re-submit stuck jobs |
| EventBridge | academy-v1-video-scan-stuck-rate | 1h — mark dead jobs |
| Ops CE | academy-v1-video-ops-ce | m6g.medium, reconcile/scanstuck |
| Ops Queue | academy-v1-video-ops-queue | EventBridge → ops jobs |

---

## Single Source of Truth

`scripts/v1/deploy.ps1` is the ONLY deploy entry point.

SSOT configuration: `docs/00-SSOT/v1/params.yaml`

GitHub Actions: `.github/workflows/video_batch_deploy.yml`
- Triggers on: `docker/video-worker/**`, `scripts/v1/**`, `docs/00-SSOT/**`
- Builds ARM64 image → pushes to ECR with SHA tag → runs deploy.ps1 with `-EcrRepoUri`

**Never** run:
- `scripts/infra/*.ps1` (legacy, removed)
- deploy.ps1 without `-EcrRepoUri` in production (immutableTagRequired=true)

---

## Deployment Steps

### Normal (CI-triggered)

Push to `main` with changes in `docker/video-worker/**` or `scripts/v1/**` or `docs/00-SSOT/**`.
The GitHub Actions workflow handles everything automatically.

### Manual (emergency)

```powershell
# Prerequisites: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION set in env
# Must have a pre-built image SHA in ECR
pwsh scripts/v1/deploy.ps1 -Env prod -EcrRepoUri "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:<sha>"
```

---

## Batch Pipeline Details

### Job Submission Flow

1. Client uploads video to R2 via presigned PUT URL
2. Client calls `POST /api/v1/media/videos/{id}/upload/complete/`
3. API validates file exists in R2, runs ffprobe
4. API calls `create_job_and_submit_batch(video)`:
   - Acquires DynamoDB lock (prevents duplicate jobs for same video)
   - Creates `VideoTranscodeJob` record (state=QUEUED)
   - Calls `batch.submit_job()` with `VIDEO_JOB_ID` in containerOverrides
5. AWS Batch queues the job → launches EC2 → starts container

### Worker Boot Sequence

```
batch_entrypoint.py
  → boto3 ssm.get_parameter("/academy/workers/env", WithDecryption=True)
  → validates 15 required keys (DB, R2, Redis, DJANGO_SETTINGS_MODULE, ...)
  → os.execvp("python", ["-m", "apps.worker.video_worker.batch_main", "<job_id>"])
```

Required SSM keys (all must be present in `/academy/workers/env`):
- `AWS_DEFAULT_REGION`
- `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_PORT`
- `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ENDPOINT`, `R2_VIDEO_BUCKET`
- `API_BASE_URL`, `INTERNAL_WORKER_TOKEN`
- `REDIS_HOST`, `REDIS_PORT`
- `DJANGO_SETTINGS_MODULE` (must be `apps.api.config.settings.worker`)

### Idempotency / Rate Limits

- DynamoDB lock: 1 active job per video_id at a time
- Tenant limit: `VIDEO_TENANT_MAX_CONCURRENT=2`
- Global limit: `VIDEO_GLOBAL_MAX_CONCURRENT=20`
- Per-video: `VIDEO_MAX_JOBS_PER_VIDEO=10`

---

## Troubleshooting

### Jobs not starting after upload

**Step 1: Check if `submit_batch_job` was called**
```
CloudWatch Logs: /aws/ecs/academy-api
Search: "BATCH_SUBMIT" | "BATCH_SUBMIT_FAILED" | "BATCH_SUBMIT_ERROR"
```

**Step 2: Check if Batch job exists and its state**
```
aws batch list-jobs --job-queue academy-v1-video-batch-queue --job-status RUNNABLE
aws batch list-jobs --job-queue academy-v1-video-batch-queue --job-status FAILED
```

**Step 3: Check compute environment health**
```
aws batch describe-compute-environments --compute-environments academy-v1-video-batch-ce
# Expected: status=VALID, state=ENABLED
```
If `status=INVALID`, re-run deploy.ps1 — it will detect INVALID and recreate the CE.

**Step 4: Check job queue state**
```
aws batch describe-job-queues --job-queues academy-v1-video-batch-queue
# Expected: state=ENABLED
```

**Step 5: Check worker logs (if job launched but failed)**
```
CloudWatch Logs: /aws/batch/academy-video-worker
# Look for: SSM fetch errors, missing keys, Django startup errors
```

**Step 6: Verify SSM parameter**
```
aws ssm get-parameter --name /academy/workers/env --with-decryption
# Must be valid JSON with all 15 required keys
```

### Jobs stuck in RUNNABLE

Possible causes:
1. **EC2 Spot capacity unavailable** — Batch will retry automatically. If stuck > 30min, check CE is not at maxvCpus.
2. **CE INVALID** — Re-run deploy.ps1.
3. **Subnet has no outbound internet** — Batch instances in private subnets need NAT or VPC endpoints for ECR/SSM/CloudWatch. Current config uses public subnets.
4. **Security group blocks ECR/CloudWatch** — Instance SG must allow outbound 443.

### Container exits immediately (exec format error)

Cause: EC2 instance launched with wrong AMI (x86 instead of ARM64).
The `ec2Configuration: ECS_AL2023` in the CE template ensures the ARM64 AMI is used.
If this occurs: the CE must be recreated (deploy.ps1 will do this if status=INVALID).

### Large video fails (disk full)

The CE uses a launch template (`academy-v1-video-batch-lt`) with a 200GB gp3 root volume.
Standard videos: 200GB. Long videos (3h+): 300GB (long CE uses `academy-v1-video-batch-long-lt`).
If the launch template is missing, the CE will use the default ECS AMI root volume (~30GB).

---

## Resource Names (SSOT)

| Resource | Name |
|---|---|
| Video CE (standard) | academy-v1-video-batch-ce |
| Video CE (long) | academy-v1-video-batch-long-ce |
| Ops CE | academy-v1-video-ops-ce |
| Video Queue (standard) | academy-v1-video-batch-queue |
| Video Queue (long) | academy-v1-video-batch-long-queue |
| Ops Queue | academy-v1-video-ops-queue |
| Job Def (standard) | academy-v1-video-batch-jobdef |
| Job Def (long) | academy-v1-video-batch-long-jobdef |
| Launch Template (standard) | academy-v1-video-batch-lt |
| Launch Template (long) | academy-v1-video-batch-long-lt |
| DynamoDB lock | academy-v1-video-job-lock |
| DynamoDB checkpoints | academy-v1-video-upload-checkpoints |
| ECR repo | academy-video-worker |
| Log group | /aws/batch/academy-video-worker |
| IAM job role | academy-video-batch-job-role |

---

## Post-Deployment Validation

```powershell
# Run from repo root with AWS credentials set
pwsh scripts/v1/deploy.ps1 -Env prod -Plan
# Shows current state of all resources without making changes
```

Manual validation checklist:
- [ ] CE status=VALID, state=ENABLED
- [ ] Queue state=ENABLED
- [ ] Job definition has ACTIVE revision
- [ ] DynamoDB table exists (`academy-v1-video-job-lock`)
- [ ] SSM parameter `/academy/workers/env` has all required keys
- [ ] Upload a test video → confirm Batch job created → confirm worker logs in CloudWatch
