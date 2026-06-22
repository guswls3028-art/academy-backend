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
| **옵션** | `-Plan`, `-PruneLegacy`, `-PurgeAndRecreate`, `-PurgeAndRecreate -DryRun`, `-AwsProfile default` |

수동 정식 배포는 `cd C:\academy\backend; pwsh scripts/v1/deploy.ps1 -AwsProfile default`.
이미지는 로컬에서 빌드하지 않고 GitHub Actions가 ECR에 올린 `:latest`를 사용한다.

---

## 루트 보조 스크립트

| 범주 | 예시 |
|------|------|
| Cloudflare/Gabia DNS | `add-cloudflare-zone*.ps1`, `get-zone-dns.ps1`, `zone-dns-*.ps1` |
| 템플릿/데이터 점검 | `seed_templates.py`, `submit_templates_review.py`, `check_data_integrity.py`, `integrity_snapshot.py` |
| legacy deploy cron 정리 | `scripts/v1/disable-legacy-deploy-crons.ps1` |

legacy hot/rapid deploy 스크립트는 live tree에서 제거했다. 운영 반영은 CI workflow 또는
`scripts/v1/deploy.ps1` 기준으로만 판단한다.

---

## 아카이브 (실행 금지, 참고용)

구 배포 스택은 **scripts/archive/** 아래에 보관했다. **실행 금지.** deploy 시 호출
스택에 archive 또는 archive/infra가 있으면 즉시 fail.

| 하위 | 설명 |
|------|------|
| **archive/v4/** | 구 SSOT v4 배포·검증 스크립트 (v1로 대체됨) |
| **archive/infra/** | 구 인프라 스크립트·JSON (v1/templates에 반영됨) |
| **archive/legacy/** | 구 scripts 루트 .ps1/.py/.sh 등 |
| **archive/redeploy/** | 구 redeploy 스크립트 |
| **archive/scripts_v3/** | 구 scripts_v3 풀스택 배포 (v1로 대체됨) |

상세: [scripts/archive/README.md](archive/README.md)

---

## 관련 문서

- 정식 SSOT 인덱스: [docs/README.md](../docs/README.md)
- 실행 SSOT 파라미터: [docs/ssot/params.yaml](../docs/ssot/params.yaml)
- 배포 아키텍처 기준: [docs/infrastructure/deployment-architecture.md](../docs/infrastructure/deployment-architecture.md)
