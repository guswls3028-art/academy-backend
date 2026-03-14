# 배포 체크리스트

**Version:** V1.1.0 | **최종 수정:** 2026-03-15

---

## 배포 전 (30초)

- [ ] 배포 대상 서비스: ( ) API / ( ) Messaging / ( ) AI / ( ) Video
- [ ] migration 포함 여부: ( ) 없음 / ( ) 있음 → **additive-only 확인** (nullable/default만 허용, drop/rename 절대 금지)
- [ ] 현재 서비스 상태 정상 확인:
  ```
  curl -s https://api.1academy.co.kr/healthz   # 200 OK?
  curl -s https://api.1academy.co.kr/health     # 200 OK?
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

## 배포 후 (1분)

- [ ] CI/CD 워크플로우 성공 확인:
  ```
  gh run list -w "v1-build-and-push-latest.yml" -L 1
  ```
- [ ] `/healthz` 200 확인:
  ```
  curl -s -o /dev/null -w "%{http_code}" https://api.1academy.co.kr/healthz
  ```
- [ ] `/health` 200 확인:
  ```
  curl -s -o /dev/null -w "%{http_code}" https://api.1academy.co.kr/health
  ```
- [ ] ASG 인스턴스 정상 확인 (GitHub Actions Summary 또는 AWS 콘솔):
  ```
  powershell -File scripts/v1/run-with-env.ps1 -- aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names academy-v1-api-asg --query "AutoScalingGroups[0].Instances[*].{Id:InstanceId,Health:HealthStatus,State:LifecycleState}" --output table
  ```
- [ ] DLQ 메시지 0개 확인:
  ```
  powershell -File scripts/v1/run-with-env.ps1 -- aws sqs get-queue-attributes --queue-url https://sqs.ap-northeast-2.amazonaws.com/{ACCOUNT}/academy-dlq --attribute-names ApproximateNumberOfMessages --output text
  ```
- [ ] 주요 기능 수동 확인: `________`

---

## 실패 시

1. CI/CD 로그 확인: `gh run view --log-failed`
2. 롤백 필요 시 → **RUNBOOK-INCIDENTS.md § 6. 배포 실패/롤백** 참조
