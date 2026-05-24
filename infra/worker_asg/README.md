# Worker ASG (Messaging / AI)

**Status:** active for Messaging and AI workers only. Video encoding is AWS Batch
only after the 2026-05-10 daemon/SQS cutover.

This directory keeps the worker ASG support code and historical Lambda helpers.
The active deployment truth is:

- CI deploy: `.github/workflows/v1-build-and-push-latest.yml`
- Formal infra reconcile: `scripts/v1/deploy.ps1`
- Worker env SQS sync: `scripts/v1/update-workers-env-sqs.ps1`

There is no active non-`v1` `deploy_worker_asg.ps1` entrypoint.

## 구성

- **queue_depth_lambda**: 1분마다 SQS visible 메시지 수를 CloudWatch `Academy/Workers` 네임스페이스에 퍼블리시 (AI = lite+basic 합산, Messaging). `ENABLE_VIDEO_METRICS=false`일 때는 폐기된 Video 큐를 조회하지 않는다.
- **user_data**: AI/Messaging Launch Template 용 부팅 스크립트 (Docker, ECR pull, 컨테이너 실행, EC2_IDLE_STOP_THRESHOLD=0). Video user_data 는 stale 잔재.
- **배포**: GitHub Actions 또는 `scripts/v1/deploy.ps1`가 정본.

## 사전 조건

1. **SSM Parameter**: `.env` 내용을 `/academy/workers/env` (SecureString)에 저장.
   ```powershell
   aws ssm put-parameter --name /academy/workers/env --type SecureString --value file://.env --overwrite --region ap-northeast-2
   ```
2. **Lambda 역할** (`academy-lambda`): 큐 깊이 Lambda용으로 **CloudWatch PutMetricData** (Namespace `Academy/Workers`) 권한 필요.  
   - `infra/worker_asg/iam_policy_queue_depth_lambda.json` 참고해 인라인 정책 추가 또는 기존 정책에 Statement 추가.
3. **EC2 IAM 역할** (인스턴스 프로필): `ssm:GetParameter` (/academy/workers/env), ECR pull, 기존 워커용 권한.
4. **VPC/서브넷/보안 그룹**: 기존 워커와 동일 (예: academy-worker-sg). 서브넷 ID 2개 이상 권장 (Multi-AZ).

## 배포 / 반영

```powershell
cd C:\academy\backend

# SSM /academy/workers/env 에 Messaging/AI 큐 이름을 현재 params SSOT 기준으로 반영
pwsh scripts/v1/update-workers-env-sqs.ps1 -AwsProfile default

# Launch Template, UserData, ASG, SSM, Batch 등 인프라 설정 정합화
pwsh scripts/v1/deploy.ps1 -AwsProfile default
```

일반 코드 배포는 backend `main` push 후 GitHub Actions가 worker ASG refresh를 수행한다.

## Lambda 사양 (academy-worker-queue-depth-metric)

| 항목 | 값 |
|------|-----|
| PackageType | Zip |
| RepositoryType | S3 |
| Runtime | python3.11 |
| Handler | lambda_function.lambda_handler |
| LastModified | 2026-02-19 |

## 생성 리소스

| 리소스 | 이름 |
|--------|------|
| Lambda | academy-worker-queue-depth-metric |
| EventBridge Rule | academy-worker-queue-depth-rate (rate 1 min) |
| Launch Template | academy-v1-ai-worker-lt, academy-v1-messaging-worker-lt (Video ASG 폐기) |
| ASG | academy-v1-ai-worker-asg, academy-v1-messaging-worker-asg (Video ASG 폐기) |
| Scaling Policy | QueueDepthTargetTracking (Target Tracking) |

## 전환 시 (기존 Lambda 스케일 제거)

1. ASG 동작 검증 후 legacy **EventBridge 규칙** `academy-worker-autoscale-rate` 비활성화 또는 삭제.
2. 워커 코드에서 **self-stop** 비활성화 또는 제거 (ASG가 terminate 담당).
3. (선택) 기존 수동 기동 워커 EC2는 종료.
