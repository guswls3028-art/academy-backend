# ssot — 코드/CI 의존 SSOT

코드·스크립트·CI 가 **경로 그대로** 읽는 단일 진실. 이동·이름변경 시 같이 바꿔야 할 곳 다수.

## 파일

| 파일 | 의미 | 의존 |
|------|------|------|
| [params.yaml](params.yaml) | 인프라 실행 파라미터 (region/cluster/asg/sqs 등) | `scripts/v1/*.ps1`, `scripts/v1/core/ssot.ps1` |
| [runtime-current.md](runtime-current.md) | 현재 운영 런타임/비용 baseline 스냅샷 | 운영 문서, 비용 점검, 배포 후 검증 |
| [identifier.md](identifier.md) | ID 체계 (discriminated union, FK 정책) | 코드 lint, FK migration |
| [path-alias-policy.md](path-alias-policy.md) | 경로 별칭 정책 (frontend/backend) | tsconfig, vite, pytest |
| [messaging-policy.md](messaging-policy.md) | 메시징 발송 정책 (TPS/재시도/실패) | messaging worker |
| [ecr-lifecycle-policy.json](ecr-lifecycle-policy.json) | ECR 이미지 보관 정책 | `scripts/v1/resources/ecr.ps1` |

## 변경 절차

1. SSOT 변경 = 의존하는 코드/스크립트/CI 동시 수정
2. dry-run 검증 → 운영 적용
3. 운영 런타임이 바뀌면 `runtime-current.md`와 관련 active runbook 동시 갱신
4. RELEASE-NOTES 에 변경 명시
