# V1 리소스 정리 계획 (비용 절감)

**리전:** ap-northeast-2 **생성:** 2026-07-06T17:31:05.0537304+09:00 **전제:** SSOT 명시 리소스 삭제 금지.

## 삭제/정리 대상

| 대상 | 삭제/동작 | 삭제 이유 | SSOT 매칭 | 예상 비용 절감 |
|------|------------|-----------|------------|-----------------|

## 실행 방법
```powershell
pwsh -NoProfile -File scripts/v1/run-resource-cleanup.ps1 -AwsProfile default   # DryRun 기본
pwsh -NoProfile -File scripts/v1/run-resource-cleanup.ps1 -AwsProfile default -Execute   # 실제 적용
```

Before cleanup, confirm this plan and aws-resource-inventory.latest.md.

