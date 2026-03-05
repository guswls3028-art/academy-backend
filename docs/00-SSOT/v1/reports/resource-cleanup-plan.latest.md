# V1 리소스 정리 계획 (비용 절감)

**Region:** ap-northeast-2 **Generated:** 2026-03-06T07:34:52.4205737+09:00 **Rule:** No delete of SSOT-listed resources.

## Cleanup targets

| Target | Action | Reason | SSOT | Cost saving |
|------|------------|-----------|------------|-----------------|
| EC2 i-07f6f245de7026361 (academy-build-arm64) | stop | Build server, start only when needed | KEEP(stop) | Instance cost when stopped |
| SG sg-0051cc8f79c04b058 (academy-api-sg) | delete | no ENI attached | LEGACY_CANDIDATE | cleanup |
| SG sg-02692600fbf8e26f7 (academy-worker-sg) | delete | no ENI attached | LEGACY_CANDIDATE | cleanup |
| SG sg-00d2fb147d61f5cd8 (academy-v1-vpce-sg) | delete | no ENI attached | LEGACY_CANDIDATE | cleanup |

## Run
```powershell
pwsh -NoProfile -File scripts/v1/run-with-env.ps1 -- pwsh -NoProfile -File scripts/v1/cleanup-legacy.ps1   # DryRun 기본
pwsh -NoProfile -File scripts/v1/run-with-env.ps1 -- pwsh -NoProfile -File scripts/v1/cleanup-legacy.ps1 -Execute   # 실제 적용
```

Before cleanup, confirm this plan and aws-resource-inventory.latest.md.

