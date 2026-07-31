# GitHub 저장소·배포 제어면 운영

**상태:** 현재 실행 계약
**적용 대상:** `academy-backend`, `academy-frontend`
**실행 정본:** `scripts/v1/converge-github-governance.ps1`, 각 저장소
`.github/workflows/`

## 1. 목적

코드 검증과 배포 권한을 저장소 설정에서도 강제한다. workflow 파일만 안전해도
기본 브랜치가 무보호이거나 Actions 태그가 이동 가능하면 같은 보장을 할 수 없다.
반대로 ruleset을 먼저 강제해 아직 존재하지 않는 check를 요구하면 merge 경로가
잠길 수 있으므로 아래 순서로만 적용한다.

## 2. 필수 상태

| 경계 | 백엔드 | 프론트엔드 |
|------|--------|------------|
| 기본 브랜치 | `main` | `main` |
| merge | PR·필수 check·review thread 해소. push 가능한 직접 유지관리자가 1명이면 승인 0, 2명 이상이면 마지막 push 작성자 외 1명 승인과 stale 승인 해제 | 동일 |
| 금지 | default branch 삭제, force push | 동일 |
| 필수 check | `Backend static and migration contract`, `Backend Django smoke and deployment contracts` | `Hangul companion Windows COM contract`, `Typecheck + Lint + Build` |
| Actions | 모든 외부 action을 40자 commit SHA로 고정, 기본 `GITHUB_TOKEN=contents:read` | 동일 |
| production | GitHub Environment `production` 승인 후 mutation 시작 | `production` 승인 후 Pages direct upload |
| preview/rollback | 해당 없음 | `preview`는 운영 mutation 없음. `production-rollback`은 승인 대기 없이 main 실패 보상만 허용 |
| 보안 업데이트 | Dependabot security updates + 주간 dependency/Actions PR | 동일 |

`academy-main-governance` ruleset의 유일한 bypass actor는 GitHub 공식
`github-actions` Integration(id `15368`)이다. 백엔드 workflow가 검증 증빙
파일을 `main`에 쓰는 현재 호환 경계 때문에 필요하다. 저장소 기본 token은
read-only이고, `contents:write`는 production workflow의 build evidence와
최종 release manifest job에만 선언한다. 다른 workflow에 write 권한을 추가할
때는 bypass 범위가 넓어지는 것으로 보고 이 문서를 함께 재검토한다.

승인 수는 저장소의 실제 direct collaborator를 읽어 수렴한다. 현재처럼 push
가능한 사람이 1명뿐이면 자기 PR을 스스로 승인할 수 없으므로 독립 승인을
요구하지 않는다. 이 경우에도 direct push가 아니라 PR을 사용하고, 필수 check와
review thread 해소는 그대로 강제된다. 두 번째 push 권한자가 추가되면 다음
수렴에서 승인 수가 1로 올라가며 마지막 push 작성자는 그 승인을 할 수 없다.

## 3. Cloudflare secret 경계

전역 API key와 계정 이메일을 workflow에 주입하지 않는다.

| secret | 최소 권한 | 사용 위치 |
|--------|-----------|-----------|
| `CLOUDFLARE_PREVIEW_API_TOKEN` | 대상 account의 Pages Edit | `preview` environment의 `candidate-preview` |
| `CLOUDFLARE_PRODUCTION_API_TOKEN` | 대상 account의 Pages Edit | `production` 및 `production-rollback` environment에 같은 이름으로 저장 |
| `CLOUDFLARE_INFRA_API_TOKEN` | 대상 account Pages Edit + `hakwonplus.com` Zone Read/DNS Edit | `production` environment의 developer custom-domain 수렴 단계만 |
| `CLOUDFLARE_ACCOUNT_ID` | 비밀값으로 기존 저장 | 세 경로 공통 |

preview와 production token은 서로 교환하지 않는다. production token을
`production-rollback`에도 별도 저장하는 이유는 post-deploy E2E 실패 보상이
두 번째 수동 승인을 기다리지 않게 하기 위해서다. 이 environment는 protected
branch만 허용하고 일반 deploy job에서는 사용하지 않는다. infra token은 일반
Wrangler deploy step에 전달하지 않는다.

## 4. 최초 적용 순서

1. 두 저장소의 변경 PR을 열고 새 workflow check가 PR head에서 실제 성공하는지
   확인한다.
2. frontend `preview`/`production`/`production-rollback` environment와
   repository secret에 위 세 Cloudflare scoped token을 준비한다. 동시에
   backend `/academy/r2/preprod/credentials`에 production key와 다른 video
   bucket Object Read/List 전용 credential을 저장하고 Cloudflare 권한
   readback을 증거로 남긴다. 이 값들이 준비되기 전에는 두 PR을 merge하지
   않는다. 기존 global key를 제거하는 것은 새 workflow가 token으로 성공한
   뒤다.
3. read-only audit를 실행한다. 미적용 상태에서는 nonzero가 정상이다.

   ```powershell
   pwsh scripts/v1/converge-github-governance.ps1
   ```

4. PR check가 성공하고 secret 준비가 끝난 직후에만 설정을 수렴한다.

   ```powershell
   pwsh scripts/v1/converge-github-governance.ps1 -Apply
   ```

5. 동일 명령을 `-Apply` 없이 다시 실행해 두 저장소 모두
   `GITHUB_GOVERNANCE_PASS`인지 확인한다.
6. 승인된 PR을 merge한다. 첫 production workflow는 environment 승인을
   요구해야 하며, 승인 전에는 AWS/Cloudflare mutation이 없어야 한다.
7. 배포 성공 후 Actions 설정의 SHA pinning, ruleset, environment reviewer,
   Dependabot security updates를 다시 읽어 증거에 기록한다.

ruleset이나 environment를 먼저 삭제하는 방식으로 복구하지 않는다. check 이름
변경이 필요하면 새 check가 성공하는 PR을 먼저 만든 뒤 ruleset required check를
같은 변경 창에서 교체한다.

## 5. 검증과 실패 처리

- `converge-github-governance.ps1` 기본 실행은 read-only이며 drift에서 실패한다.
- `-Apply`는 ruleset, Actions 기본 권한/SHA pinning, production/preview
  environment, Dependabot security updates만 변경한다. 코드 push, merge,
  workflow 실행 승인은 하지 않는다.
- production environment 승인이 보이지 않으면 배포를 계속하지 않는다.
- frontend token 오류는 global key 복귀보다 token scope/account/project binding을
  먼저 교정한다.
- GitHub plan/API 제약으로 environment reviewer 또는 ruleset 저장이 거부되면
  적용 성공으로 간주하지 말고 해당 API 응답을 보존한다.

## 6. 관련 문서

- [deployment-modes.md](deployment-modes.md)
- [formal-deploy.md](formal-deploy.md)
- frontend `docs/DEPLOYMENT-OPERATIONS.md`
