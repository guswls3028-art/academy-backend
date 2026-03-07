# Messaging/AI 워커 SQS IAM 수정 리포트

**날짜:** 2026-03-07  
**원인:** academy-ec2-role에 SQS 수신 권한 없음 → Messaging/AI 워커가 큐에서 메시지 수신 불가

---

## 1. 조치 내용

### 1.1 IAM 정책 추가
- **파일:** `scripts/v1/templates/iam/policy_workers_sqs.json` (신규)
- **역할:** academy-ec2-role (API, Messaging, AI 워커 공용)
- **권한:** `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:ChangeMessageVisibility`, `sqs:GetQueueAttributes`, `sqs:GetQueueUrl`
- **대상 큐:** `academy-v1-messaging-queue`, `academy-v1-ai-queue`

### 1.2 iam.ps1 수정
- `Ensure-EC2InstanceProfileSSM`에서 `academy-workers-sqs` 인라인 정책 부여 추가
- 배포 시 자동 적용

### 1.3 SSM workers env 갱신
- `update-workers-env-sqs.ps1` 실행으로 `/academy/workers/env`에 큐 이름 반영
- `MESSAGING_SQS_QUEUE_NAME=academy-v1-messaging-queue`
- `AI_SQS_QUEUE_NAME_*=academy-v1-ai-queue`

---

## 2. 적용 상태

| 항목 | 상태 |
|------|------|
| IAM policy academy-workers-sqs | ✅ academy-ec2-role에 적용됨 |
| SSM /academy/workers/env | ✅ 갱신됨 |
| Redis/RDS SG | ✅ sg-data에 sg-app 5432/6379 허용 (기존 Ensure 로직) |

---

## 3. 워커 재시작 (권장)

IAM 권한은 **즉시 적용**되지만, SSM env는 **인스턴스 부팅 시** 로드됩니다.  
기존 워커가 잘못된 큐 이름으로 폴링 중이었다면 instance-refresh로 재시작하세요.

```powershell
# Messaging 워커 ASG instance-refresh
aws autoscaling start-instance-refresh --auto-scaling-group-name academy-v1-messaging-worker-asg --region ap-northeast-2 --profile default

# AI 워커 ASG instance-refresh
aws autoscaling start-instance-refresh --auto-scaling-group-name academy-v1-ai-worker-asg --region ap-northeast-2 --profile default
```

---

## 4. 검증

1. **SQS 큐 메시지 수:** CloudWatch 또는 AWS 콘솔에서 `ApproximateNumberOfMessages` 확인
2. **워커 로그:** SSM Session Manager로 워커 인스턴스 접속 후 `docker logs`로 SQS 폴링/처리 로그 확인
3. **메시징/AI 기능:** 실제 메시지 전송, 엑셀 학생등록 등으로 E2E 테스트
