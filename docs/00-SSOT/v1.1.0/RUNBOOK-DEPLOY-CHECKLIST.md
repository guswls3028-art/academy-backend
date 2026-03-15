# 배포 체크리스트

**Version:** V1.1.0 | **최종 수정:** 2026-03-15

---

## 배포 전 (30초)

- [ ] 배포 대상 서비스: ( ) API / ( ) Messaging / ( ) AI / ( ) Video
- [ ] migration 포함 여부: ( ) 없음 / ( ) 있음 → **additive-only 확인** (nullable/default만 허용, drop/rename 절대 금지)
- [ ] 현재 서비스 상태 정상 확인:
  ```
  curl -s https://api.hakwonplus.com/healthz   # 200 OK?
  curl -s https://api.hakwonplus.com/health     # 200 OK?
  ```
- [ ] 마지막 성공 commit SHA 기록: `________`
  ```
  gh run list -w "v1-build-and-push-latest.yml" -L 3 --json headSha,status,conclusion
  ```

---

## 배포 실행

```bash
git push origin main
# CI/CD가 자동으로: detect → build → migrate → deploy → verify
```

---

## 배포 후 — 필수 검증 (CRITICAL)

> **워커가 안 돌고 있으면 배포 성공이 아니다.**
> 워커 장애는 사일런트 장애 — API는 200 반환하면서 SQS에 잡만 쌓이고, 사용자는 "영상이 안 나와요" "알림이 안 와요"만 보고한다.

### Step 1: CI + API Health (30초)

- [ ] CI/CD 워크플로우 성공 확인:
  ```
  gh run list -w "v1-build-and-push-latest.yml" -L 1
  ```
- [ ] `/healthz` 200 확인:
  ```
  curl -s -o /dev/null -w "%{http_code}" https://api.hakwonplus.com/healthz
  ```
- [ ] `/health` 200 확인:
  ```
  curl -s -o /dev/null -w "%{http_code}" https://api.hakwonplus.com/health
  ```

### Step 2: 워커 컨테이너 상태 (CRITICAL — 반드시 확인)

- [ ] **3개 ASG 인스턴스 전부 Healthy + InService:**
  ```bash
  for ASG in academy-v1-api-asg academy-v1-messaging-worker-asg academy-v1-ai-worker-asg; do
    echo "=== $ASG ==="
    powershell -File scripts/v1/run-with-env.ps1 -- aws autoscaling describe-auto-scaling-groups \
      --auto-scaling-group-names "$ASG" --region ap-northeast-2 \
      --query "AutoScalingGroups[0].{Desired:DesiredCapacity,Instances:Instances[*].[InstanceId,HealthStatus,LifecycleState]}" \
      --output json
  done
  ```

- [ ] **워커 컨테이너 docker ps — 각각 (healthy) 상태:**
  SSM으로 각 워커 인스턴스에 `docker ps` 실행하여 확인:
  - `academy-api` → **(healthy)**
  - `academy-messaging-worker` → **(healthy)**
  - `academy-ai-worker-cpu` → **(healthy)**

### Step 3: SQS 큐 + DLQ (적체/실패 확인)

- [ ] **Messaging 큐 — Messages=0, DLQ=0:**
  ```bash
  powershell -File scripts/v1/run-with-env.ps1 -- aws sqs get-queue-attributes \
    --queue-url https://sqs.ap-northeast-2.amazonaws.com/809466760795/academy-v1-messaging-queue \
    --attribute-names All --region ap-northeast-2 \
    --query "Attributes.{Messages:ApproximateNumberOfMessages,InFlight:ApproximateNumberOfMessagesNotVisible}" --output json
  ```
- [ ] **AI 큐 — Messages=0, DLQ=0:**
  ```bash
  powershell -File scripts/v1/run-with-env.ps1 -- aws sqs get-queue-attributes \
    --queue-url https://sqs.ap-northeast-2.amazonaws.com/809466760795/academy-v1-ai-queue \
    --attribute-names All --region ap-northeast-2 \
    --query "Attributes.{Messages:ApproximateNumberOfMessages,InFlight:ApproximateNumberOfMessagesNotVisible}" --output json
  ```
- [ ] **DLQ 0개 확인** (> 0이면 즉시 조사):
  ```bash
  for DLQ in academy-v1-messaging-queue-dlq academy-v1-ai-queue-dlq; do
    echo "$DLQ:"
    powershell -File scripts/v1/run-with-env.ps1 -- aws sqs get-queue-attributes \
      --queue-url "https://sqs.ap-northeast-2.amazonaws.com/809466760795/$DLQ" \
      --attribute-names All --region ap-northeast-2 \
      --query "Attributes.ApproximateNumberOfMessages" --output text
  done
  ```

### Step 4: 기능 확인

- [ ] 주요 기능 수동 확인: `________`

---

## 실패 시

1. CI/CD 로그 확인: `gh run view --log-failed`
2. 롤백 필요 시 → **RUNBOOK-INCIDENTS.md § 6. 배포 실패/롤백** 참조
