# Academy SSOT v4 — Runbook

**역할:** 장애/운영 점검 시 최소 커맨드셋(복붙 가능).

---

## 1. API health

```powershell
Invoke-WebRequest -Uri "http://15.165.147.157:8000/health" -UseBasicParsing -TimeoutSec 10
# 200 OK 기대
```

---

## 2. SSM sync 확인

```powershell
aws ssm get-parameter --name /academy/workers/env --region ap-northeast-2 --query Parameter.Name --output text
aws ssm get-parameter --name /academy/api/env --region ap-northeast-2 --query Parameter.Name --output text
```

---

## 3. ASG 상태

```powershell
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names academy-messaging-worker-asg academy-ai-worker-asg --region ap-northeast-2 --output table
```

---

## 4. Batch RUNNABLE stuck

```powershell
aws batch list-jobs --job-queue academy-video-batch-queue --job-status RUNNABLE --region ap-northeast-2 --output json
aws batch list-jobs --job-queue academy-video-ops-queue --job-status RUNNABLE --region ap-northeast-2 --output json
```

---

## 5. EventBridge target 확인

```powershell
aws events list-targets-by-rule --rule academy-reconcile-video-jobs --region ap-northeast-2
aws events list-targets-by-rule --rule academy-video-scan-stuck-rate --region ap-northeast-2
```

---

## 6. Drift / Evidence

```powershell
pwsh scripts/v4/deploy.ps1 -Plan
# drift 표 + Evidence 스타일 출력
```

---

## 7. 복구(재배포)

```powershell
pwsh scripts/v4/deploy.ps1
# Netprobe SUCCEEDED 확인
```
