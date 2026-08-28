# scripts — 스크립트 진입점

**진입점은 이 파일.** 인프라 배포·검증의 정식 경로는 **scripts/v1** 만 사용한다.
루트의 개별 스크립트는 DNS, 템플릿, 데이터 점검 같은 보조 작업용이며
배포 SSOT가 아니다.

---

## 정식 인프라 배포·검증

| 용도 | 경로 |
|------|------|
| **배포** | `scripts/v1/deploy.ps1` |
| **새 PC 준비** | `scripts/v1/bootstrap.ps1` |
| **검증(5단계)** | `scripts/v1/verify.ps1` → reports/verify.latest.md |
| **배포 후 검증** | `scripts/v1/run-production-canary.ps1`, `scripts/v1/run-deploy-verification.ps1` |
| **옵션** | 운영: `-Plan`, `-AwsProfile default`. 삭제·복구성 옵션은 Plan/비운영 전용이며 정상 운영 mutation에서 거부된다. |

수동 정식 배포는 `cd C:\academy\backend; pwsh scripts/v1/deploy.ps1 -AwsProfile default`.
이미지는 로컬에서 빌드하지 않고, GitHub Actions가 preprod와 운영 런타임 검증을
마친 `release-manifest.latest.json`의 digest를 사용한다. `:latest`는 검증 성공
후 같은 digest로 이동하는 호환 alias이며 배포 입력이 아니다.

---

## 루트 보조 스크립트

| 범주 | 예시 |
|------|------|
| Cloudflare/Gabia DNS | `add-cloudflare-zone*.ps1`, `get-zone-dns.ps1`, `zone-dns-*.ps1` |
| 템플릿/데이터 점검 | `seed_templates.py`, `submit_templates_review.py`, `check_data_integrity.py`, `integrity_snapshot.py` |
| legacy deploy cron 정리 | `scripts/v1/disable-legacy-deploy-crons.ps1` |

동시 Codex 작업은 `scripts/codex/session-worktree.ps1`로 세션별 worktree를
생성·점검·정리한다. 이 스크립트는 제품 배포 경로가 아니며, dirty 또는
`origin/main`에 미병합된 worktree를 자동 삭제하지 않는다. 계약 테스트는
`pwsh scripts/codex/test-session-worktree.ps1`이다.

변경 경로별 기존 검증 누락은
`pwsh scripts/codex/get-change-risk-plan.ps1 [-RunLocalGates]`로 확인한다.
backend/frontend 제품 변경의 최종 운영 증거는 release owner가
`scripts/codex/assert-production-release-bundle.ps1`로 각 exact SHA, 공식
GitHub run, pending approval, backend manifest·Dynamo lock, frontend live
version을 fail-closed 검증한다. 이 readback은 별도 작업 큐나 릴리스 SSOT를
만들지 않는다.

legacy hot/rapid deploy 스크립트는 live tree에서 제거했다. 운영 반영은 CI workflow 또는
`scripts/v1/deploy.ps1` 기준으로만 판단한다.

---

## 폐기된 배포 스택

v3·v4와 구 인프라 스크립트는 `scripts/v1/`에 필요한 계약이 반영된 뒤 live
tree에서 제거했다. 과거 구현은 Git 이력에서만 조회한다. 현재 배포 코드가
`scripts/archive/`를 호출하려 하면 `scripts/v1/core/guard.ps1`이 계속
fail-closed 한다.

---

## 관련 문서

- 정식 SSOT 인덱스: [docs/README.md](../docs/README.md)
- 실행 SSOT 파라미터: [docs/ssot/params.yaml](../docs/ssot/params.yaml)
- 배포 아키텍처 기준: [docs/infrastructure/deployment-architecture.md](../docs/infrastructure/deployment-architecture.md)
