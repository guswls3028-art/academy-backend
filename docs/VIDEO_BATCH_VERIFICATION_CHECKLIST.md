# Video Batch Refactor — Verification Checklist

> **최신 인프라 상태 기준**

## Smoke Test

1. **업로드 5개** → 5개 VideoTranscodeJob row 생성 (state=QUEUED)
2. **Batch 제출** → 5개 AWS Batch Job 제출
3. **각 Batch job** → 1회만 실행 후 종료
4. **DB 상태** → Job SUCCEEDED, Video READY
5. **유휴 시** → Batch compute vCPU 0으로 축소

## 사전 조건

### 인프라 설정 (한 번에)

```powershell
cd C:\academy
.\scripts\infra\batch_video_setup_full.ps1
```

- VPC/Subnet/SecurityGroup 자동 탐색 → setup → retryStrategy 검증
- 파라미터 없이 실행 가능 (Region=ap-northeast-2, ECR URI 기본값)

### 값 직접 지정

```powershell
.\scripts\infra\batch_video_setup_full.ps1 -VpcId "vpc-xxx" -SubnetIds @("subnet-a","subnet-b") -SecurityGroupId "sg-xxx"
```

### retryStrategy 검증만

```powershell
.\scripts\infra\batch_video_verify_and_register.ps1 -Region ap-northeast-2 -EcrRepoUri 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
```

### 체크리스트

- [ ] batch_video_setup_full.ps1 또는 batch_video_setup.ps1 실행
- [ ] retryStrategy.attempts == 1 확인 (batch_video_verify_and_register.ps1)
- [ ] ECR에 academy-video-worker:latest 푸시
- [ ] API에 VIDEO_BATCH_JOB_QUEUE, VIDEO_BATCH_JOB_DEFINITION 설정
- [ ] Batch Job Role에 SSM (academy/*), ECR, CloudWatch Logs 권한

## 삭제된 레거시 파일

| 파일 | 비고 |
|------|------|
| scripts/infra/apply_video_asg_scaling_policy.ps1 | DEPRECATED |
| scripts/video_worker_scaling_sqs_direct.ps1 | DEPRECATED |
| apps/worker/video_worker/sqs_main.py | 인코딩 경로 삭제 (delete_r2는 Lambda) |

## 인코딩 경로

- **사용**: create_job_and_submit_batch (video_encoding.py)
- **미사용**: create_job_and_enqueue, enqueue_by_job
