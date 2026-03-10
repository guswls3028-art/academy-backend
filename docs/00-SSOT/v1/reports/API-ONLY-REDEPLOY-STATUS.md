# API만 재배포 상태 확인 요약

**확인 시각:** 2026-03-11 (KST)  
**Instance Refresh ID:** `28b31570-88d0-408e-842b-4499c6e1d25d`

---

## 1. 배포(API instance refresh) 상태

| 항목 | 상태 |
|------|------|
| **Instance Refresh** | **InProgress** (50%) — 새 인스턴스 `i-0d4162d7f293c2e71` 워밍업 중 |
| **ALB 타깃** | 1개 draining(구 인스턴스), 1개 unhealthy(신 인스턴스, 헬스체크 대기) |
| **결론** | 리프레시 **진행 중**. 완료되면 새 인스턴스가 healthy로 전환되고, 구 인스턴스는 제거됨. |

**확실한 성공 조건:**  
- `describe-instance-refreshes`에서 해당 Refresh의 `Status`가 **Successful**  
- `describe-target-health`에서 **최소 1개 타깃이 healthy**  
- `https://api.hakwonplus.com/healthz` 또는 ALB DNS `/healthz` → **HTTP 200**

---

## 2. 워커·연결 상태 (모두 정상)

| 컴포넌트 | 확인 결과 |
|----------|-----------|
| **SSM /academy/api/env** | VIDEO_BATCH_* 5개 v1 이름 일치 |
| **SSM /academy/workers/env** | 존재, REDIS_HOST·SQS 큐 이름·VIDEO_BATCH_*·API_BASE_URL 등 필수 키 있음 |
| **Redis (academy-v1-redis)** | available |
| **API ASG** | academy-v1-api-asg — desired 1, 인스턴스 2(리프레시 중) |
| **Messaging Worker ASG** | academy-v1-messaging-worker-asg — desired 1, 인스턴스 1 |
| **AI Worker ASG** | academy-v1-ai-worker-asg — desired 1, 인스턴스 1 |
| **Batch 큐/JobDef/CE** | academy-v1-video-batch-queue, academy-v1-video-batch-jobdef, academy-v1-video-batch-ce 존재 |
| **메시징 SQS** | academy-v1-messaging-queue — 메시지 0, 비가시 0 |
| **DLQ** | Messaging DLQ 0, AI DLQ 0 (검증 보고서 기준 PASS) |

**결론:** API를 제외한 모든 워커·인프라·연결 참조는 정상. API만 instance refresh 진행 중이라 일시적으로 ALB target 0 healthy.

---

## 3. 재확인 방법 (리프레시 완료 후)

```powershell
# 1) Instance refresh 완료 여부
aws autoscaling describe-instance-refreshes --auto-scaling-group-name academy-v1-api-asg --instance-refresh-ids 28b31570-88d0-408e-842b-4499c6e1d25d --region ap-northeast-2 --profile default --query "InstanceRefreshes[0].Status" --output text

# 2) API 타깃 헬스
aws elbv2 describe-target-health --target-group-arn "arn:aws:elasticloadbalancing:ap-northeast-2:809466760795:targetgroup/academy-v1-api-tg/2c34b94ea3c33101" --region ap-northeast-2 --profile default --query "TargetHealthDescriptions[*].TargetHealth.State" --output text

# 3) 비디오/배치 연결 검증
pwsh -File scripts/v1/verify-video-batch-connection.ps1
```

리프레시가 **Successful**이고 타깃이 **1개 이상 healthy**이면 API만 재배포가 **확실히 성공**한 상태로 보면 됨.
