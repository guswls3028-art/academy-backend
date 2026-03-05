# V1 Cleanup Run

**RunAt:** (run cleanup-legacy.ps1 to update) **Mode:** DryRun **Region:** ap-northeast-2

## Summary
| Action | Count |
|--------|-------|
| EIP released | 0 |
| EBS volumes deleted | 0 |
| SGs deleted | 0 |
| Build stopped | 0 |
| EC2 terminated | 0 |
| ASGs removed | 0 |
| Errors | 0 |

실행: `pwsh -NoProfile -File scripts/v1/run-with-env.ps1 -- pwsh -NoProfile -File scripts/v1/cleanup-legacy.ps1` (DryRun) / `-Execute` 적용 시 위 내용 갱신.
