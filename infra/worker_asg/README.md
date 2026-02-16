# Worker ASG (Target Tracking, Min=0)

SQS 큐 깊이 기반 ASG 오토스케일링. 설계: [ARCH_CHANGE_PROPOSAL_LAMBDA_TO_ASG.md](../../docs/SSOT_0215/IMPORTANT/ARCH_CHANGE_PROPOSAL_LAMBDA_TO_ASG.md)

## 구성

- **queue_depth_lambda**: 1분마다 SQS visible 메시지 수를 CloudWatch `Academy/Workers` 네임스페이스에 퍼블리시 (AI = lite+basic 합산, Video = academy-video-jobs).
- **user_data**: Launch Template용 부팅 스크립트 (Docker, ECR pull, 컨테이너 실행, EC2_IDLE_STOP_THRESHOLD=0).
- **배포**: `scripts/deploy_worker_asg.ps1`

## 사전 조건

1. **SSM Parameter**: `.env` 내용을 `/academy/workers/env` (SecureString)에 저장.
   ```powershell
   aws ssm put-parameter --name /academy/workers/env --type SecureString --value file://.env --overwrite --region ap-northeast-2
   ```
2. **Lambda 역할** (`academy-lambda`): 큐 깊이 Lambda용으로 **CloudWatch PutMetricData** (Namespace `Academy/Workers`) 권한 필요.  
   - `infra/worker_asg/iam_policy_queue_depth_lambda.json` 참고해 인라인 정책 추가 또는 기존 정책에 Statement 추가.
3. **EC2 IAM 역할** (인스턴스 프로필): `ssm:GetParameter` (/academy/workers/env), ECR pull, 기존 워커용 권한.
4. **VPC/서브넷/보안 그룹**: 기존 워커와 동일 (예: academy-worker-sg). 서브넷 ID 2개 이상 권장 (Multi-AZ).

## 배포

```powershell
cd C:\academy
.\scripts\deploy_worker_asg.ps1 `
  -SubnetIds "subnet-xxx,subnet-yyy" `
  -SecurityGroupId "sg-xxx" `
  -IamInstanceProfileName "academy-ec2-role"
```

선택: `-MaxCapacity 10`, `-TargetMessagesPerInstance 20`, `-KeyName "my-key"`, `-AmiId "ami-xxx"`.

## 생성 리소스

| 리소스 | 이름 |
|--------|------|
| Lambda | academy-worker-queue-depth-metric |
| EventBridge Rule | academy-worker-queue-depth-rate (rate 1 min) |
| Launch Template | academy-ai-worker-asg, academy-video-worker-asg |
| ASG | academy-ai-worker-asg, academy-video-worker-asg |
| Scaling Policy | QueueDepthTargetTracking (Target Tracking) |

## 전환 시 (기존 Lambda 스케일 제거)

1. ASG 동작 검증 후 **EventBridge 규칙** `academy-worker-autoscale-rate` 비활성화 또는 삭제.
2. 워커 코드에서 **self-stop** 비활성화 또는 제거 (ASG가 terminate 담당).
3. (선택) 기존 수동 기동 워커 EC2는 종료.
