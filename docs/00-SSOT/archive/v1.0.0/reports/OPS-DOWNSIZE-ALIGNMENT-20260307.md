# Ops 인프라 정합성 리포트 (2026-03-07)

## FACT REPORT

### SSOT (params.yaml)
| 항목 | 값 | 비고 |
|------|-----|------|
| opsInstanceType | m6g.medium | ECS_AL2023: t4g 미지원 |
| opsMaxvCpus | 2 | 1 instance (m6g.medium=2 vCPU) |
| minvCpus | 0 | scale to 0 when idle |
| reconcileSchedule | rate(1 hour) | |
| scanStuckSchedule | rate(1 hour) | |

### AWS 실제 상태 (검증 완료)
| 리소스 | 상태 |
|--------|------|
| Ops CE academy-v1-video-ops-ce | VALID, ENABLED, m6g.medium, maxvCpus=2, minvCpus=0 |
| Ops Queue academy-v1-video-ops-queue | ENABLED |
| EventBridge academy-v1-reconcile-video-jobs | rate(1 hour), ENABLED |
| EventBridge academy-v1-video-scan-stuck-rate | rate(1 hour), ENABLED |

---

## FILES CHANGED

| 파일 | 변경 내용 |
|------|-----------|
| docs/00-SSOT/v1/params.yaml | ops 주석 정리 (ECS_AL2023 t4g 미지원 명시) |
| scripts/v1/resources/eventbridge.ps1 | 스케줄 drift 시 put-rule로 갱신 |
| scripts/v1/core/prune.ps1 | fallback "rate(15 minutes)" → "rate(1 hour)" |
| docs/infra/aws-inventory.json | EventBridge 스케줄 rate(1 hour)로 정정 |

---

## SETTINGS BEFORE → AFTER

| 항목 | Before | After |
|------|--------|-------|
| Ops CE | 삭제됨 (drift 재생성 중) | m6g.medium, maxvCpus=2, minvCpus=0 |
| EventBridge reconcile | rate(1 hour) | rate(1 hour) (유지) |
| EventBridge scan-stuck | rate(1 hour) | rate(1 hour) (유지) |
| prune.ps1 fallback | rate(15 minutes) | rate(1 hour) |

---

## t4g 시도 결과

- **params에 t4g.small 적용 시:** AWS Batch CreateComputeEnvironment 실패
- **오류:** `Instance type can only be one of [..., m6g.medium, ...]` — t4g.small 미지원
- **결론:** ECS_AL2023 + ap-northeast-2에서 t4g 계열 미지원. m6g.medium 유지.

---

## VERIFICATION RESULTS

```
Ops CE: status=VALID, state=ENABLED, instanceTypes=[m6g.medium], maxvCpus=2, minvCpus=0
Ops Queue: ENABLED
EventBridge reconcile: rate(1 hour)
EventBridge scan-stuck: rate(1 hour)
```

---

## FINAL STATUS: OPS_DOWNSIZED_AND_ALIGNED

- Ops CE: 1 instance max (m6g.medium, 2 vCPU), minvCpus=0
- EventBridge: rate(1 hour) 양쪽 규칙
- SSOT, deploy 스크립트, AWS 실제 상태 정합
