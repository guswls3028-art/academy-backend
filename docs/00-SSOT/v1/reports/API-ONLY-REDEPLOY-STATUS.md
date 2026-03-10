# API만 재배포 상태 확인 요약

**확인 시각:** 2026-03-11 (KST)  
**Instance Refresh ID:** `28b31570-88d0-408e-842b-4499c6e1d25d`

---

## 1. 배포(API instance refresh) 상태

| 항목 | 상태 |
|------|------|
| **Instance Refresh** | **Successful** (완료) |
| **ALB 타깃** | 1개 **unhealthy** (헬스체크 400으로 인해 미통과) |
| **원인** | ALB 타깃 헬스체크가 타깃에 요청 시 **Host: private IP**(172.30.x.x)를 사용함. Django `ALLOWED_HOSTS`에 해당 IP가 없어 **400 Bad Request** 반환 → unhealthy. |

**적용한 수정(코드 반영 완료):** prod.py에 `ALLOWED_HOSTS` VPC 대역 172.30.0.0/22 추가. middleware.py에서 헬스체크 경로 정규화 적용.

**수정이 반영된 이미지 배포 방법:**

- **이미지 빌드·ECR 푸시는 GitHub Actions로만 수행한다.** (룰 `07_deployment_orchestrator.mdc`, Runbook §0.)
- **방법:** 수정 반영 커밋을 **main에 푸시**하면 CI(`v1-build-and-push-latest.yml`)가 academy-api 이미지 빌드·ECR 푸시 후 API ASG instance refresh를 자동 실행한다. 리프레시 완료 후 타깃이 healthy·healthz 200이면 최종 완료.

---

## 2. 워커·연결 상태 (모두 정상)

| 컴포넌트 | 확인 결과 |
|----------|-----------|
| **SSM /academy/api/env** | VIDEO_BATCH_* 5개 v1 이름 일치 |
| **SSM /academy/workers/env** | 존재, REDIS_HOST·SQS·VIDEO_BATCH_*·API_BASE_URL 등 필수 키 있음 |
| **Redis (academy-v1-redis)** | available |
| **API ASG** | academy-v1-api-asg — desired 1, 인스턴스 1 |
| **Messaging Worker ASG** | academy-v1-messaging-worker-asg — desired 1, 인스턴스 1 |
| **AI Worker ASG** | academy-v1-ai-worker-asg — desired 1, 인스턴스 1 |
| **Batch 큐/JobDef/CE** | academy-v1-video-batch-queue, academy-v1-video-batch-jobdef, academy-v1-video-batch-ce 존재 |
| **메시징 SQS** | academy-v1-messaging-queue — 메시지 0, 비가시 0 |
| **DLQ** | Messaging DLQ 0, AI DLQ 0 (검증 보고서 기준 PASS) |

**결론:** API 타깃만 unhealthy(원인 수정 반영 완료, 이미지 재배포 대기). 그 외 워커·인프라·연결 참조는 모두 정상.

---

## 3. 최종 성공 확인 방법 (수정 반영 이미지 배포 후)

1. 위 방법으로 수정 반영 이미지 배포(또는 main 푸시 후 CI 완료 대기).
2. 아래 명령으로 재확인.

```powershell
# 1) API 타깃 헬스 (1개 이상 healthy 기대)
aws elbv2 describe-target-health --target-group-arn "arn:aws:elasticloadbalancing:ap-northeast-2:809466760795:targetgroup/academy-v1-api-tg/2c34b94ea3c33101" --region ap-northeast-2 --profile default --query "TargetHealthDescriptions[*].TargetHealth.State" --output text

# 2) healthz 200 확인
curl -s -o NUL -w "%{http_code}" https://api.hakwonplus.com/healthz

# 3) 비디오/배치 연결 검증
pwsh -File scripts/v1/verify-video-batch-connection.ps1
```

타깃이 **healthy**이고 **healthz가 200**이면 API만 재배포 및 워커 연결까지 **최종 성공**으로 보면 됨.
