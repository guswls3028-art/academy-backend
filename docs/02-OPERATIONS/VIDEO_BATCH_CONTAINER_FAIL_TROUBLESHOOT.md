# Video Batch Container FAIL - Troubleshooting

## 1) Fetch CloudWatch Logs

```powershell
# Get log stream name
$jobId = "945a6ec5-b520-4fc8-bda2-f16d854d2998"
$stream = aws batch describe-jobs --jobs $jobId --region ap-northeast-2 --query "jobs[0].container.logStreamName" --output text

# Fetch logs
aws logs get-log-events --log-group-name /aws/batch/academy-video-worker --log-stream-name $stream --region ap-northeast-2 --limit 50
```

## 2) Root Cause (Structure)

`video_job_definition.json` has **empty** `environment` and `secrets`:

```json
"environment": [],
"secrets": [],
```

Batch container gets **no env vars**. Worker needs:

| Category | Required Env | Purpose |
|----------|--------------|---------|
| DB | DB_HOST, DB_NAME, DB_USER, DB_PASSWORD | Django/job_get_by_id |
| R2 | R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, R2_VIDEO_BUCKET | Download/upload, boto3 |
| Redis | REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB | RedisProgressAdapter, cache_video_status |
| API | API_BASE_URL, INTERNAL_WORKER_TOKEN | load_config, API calls |
| Temp | VIDEO_WORKER_TEMP_DIR, VIDEO_WORKER_LOCK_DIR | /tmp write (ffmpeg output) |

## 3) Top 3 Failure Causes (HakwonPlus)

1. **ENV not injected** – JobDefinition env/secrets empty → R2_ACCESS_KEY missing → config error → exit 1
2. **R2 endpoint / network** – CE SG blocks outbound HTTPS, or DNS fail
3. **/tmp permissions** – ECS_AL2023 default user may not be root; ffmpeg write fails

## 4) Fix (Applied)

- **batch_entrypoint.py**: 컨테이너 시작 시 SSM `/academy/workers/env` fetch → os.environ에 설정 → batch_main 실행
- **Dockerfile**: ENTRYPOINT로 batch_entrypoint 사용, `/tmp/video-worker` 권한 확보

필수: SSM `/academy/workers/env`에 DB_*, R2_*, REDIS_*, INTERNAL_WORKER_TOKEN, API_BASE_URL 등 전체 .env 내용 존재.

## 5) Verify

After fix, re-run verify:

```powershell
.\scripts\infra\batch_video_verify_and_register.ps1 -Region ap-northeast-2 -EcrRepoUri 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
```
