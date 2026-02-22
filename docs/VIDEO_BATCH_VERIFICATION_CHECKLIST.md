# Video Batch Refactor — Verification Checklist

## Smoke Test

1. **업로드 5개** → 5개 VideoTranscodeJob row 생성 (state=QUEUED)
2. **Batch 제출** → 5개 AWS Batch Job 제출
3. **각 Batch job** → 1회만 실행 후 종료
4. **DB 상태** → Job SUCCEEDED, Video READY
5. **유휴 시** → Batch compute vCPU 0으로 축소

## 사전 조건

### 인프라 없을 때 (한 번에 설정)

```powershell
cd C:\academy
.\scripts\infra\batch_video_setup_full.ps1
```

- VPC/Subnet/SecurityGroup 자동 탐색 후 setup + retryStrategy 검증까지 실행
- 파라미터 없이 실행 가능 (기본 Region=ap-northeast-2, ECR URI=809466760795...)

### 값 직접 지정

```powershell
.\scripts\infra\batch_video_setup_full.ps1 -VpcId "vpc-xxx" -SubnetIds @("subnet-a","subnet-b") -SecurityGroupId "sg-xxx"
```

### 탐색만 (실제 생성 안 함)

```powershell
.\scripts\infra\batch_video_setup_full.ps1 -DiscoverOnly
```

### 개별 스크립트

- [ ] `scripts/infra/batch_video_setup.ps1` 실행 (또는 batch_video_setup_full.ps1 사용)
- [ ] ECR에 academy-video-worker:latest 푸시
- [ ] API에 VIDEO_BATCH_JOB_QUEUE, VIDEO_BATCH_JOB_DEFINITION 설정 (또는 기본값 사용)
- [ ] Batch Job Role에 SSM (academy/*), ECR, CloudWatch Logs 권한
- [ ] Batch 컨테이너에 R2, DB, Redis env/SSM 전달

## 삭제된 레거시 파일 (참고)

| 파일 | 비고 |
|------|------|
| scripts/infra/apply_video_asg_scaling_policy.ps1 | DEPRECATED |
| scripts/video_worker_scaling_sqs_direct.ps1 | DEPRECATED |
| scripts/apply_video_worker_scaling_fix.ps1 | DEPRECATED |
| scripts/apply_video_visible_only_tt.ps1 등 | DEPRECATED |
| infra/worker_asg/video-visible-tt.json | video ASG용, 사용 안 함 |
| apps/worker/video_worker/sqs_main.py | 인코딩 경로 DEPRECATED (delete_r2는 별도 Lambda) |

## Grep 검사 (레거시 잔존 확인)

```powershell
# academy-video-worker-asg 스케일링 정책 스크립트 참조
rg "academy-video-worker-asg" scripts/ --glob "*.ps1"

# BacklogCount video 메트릭
rg "BacklogCount|backlog-count" --glob "*.py"

# SQS video 인코딩 enqueue
rg "enqueue_by_job|create_job_and_enqueue" apps/
```

인코딩 경로: create_job_and_submit_batch 사용. enqueue_by_job는 create_job_and_enqueue(deprecated) 내부에서만.
