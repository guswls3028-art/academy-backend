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

개인 계정 소유 저장소 ruleset은 소유 조직에 속하지 않은 GitHub App을 bypass
actor로 받을 수 없다. 따라서 백엔드 `academy-main-governance`의 유일한 bypass는
`academy-release-manifest-actions` write deploy key이고, private key는 값 조회 없이
`ACADEMY_RELEASE_DEPLOY_KEY` Actions secret에만 저장한다. 이 키는 production
workflow가 검증 증빙과 최종 release manifest를 `main`에 쓰는 현재 호환 경계에만
사용한다. 프론트엔드 ruleset에는 bypass actor가 없다. 저장소 기본 token은
read-only이고, deploy key를 받는 checkout은 두 evidence-push job으로 제한한다.
다른 workflow에 write 경계를 추가할 때는 bypass 범위가 넓어지는 것으로 보고 이
문서를 함께 재검토한다.

승인 수는 저장소의 실제 direct collaborator를 읽어 수렴한다. 현재처럼 push
가능한 사람이 1명뿐이면 자기 PR을 스스로 승인할 수 없으므로 독립 승인을
요구하지 않는다. 이 경우에도 direct push가 아니라 PR을 사용하고, 필수 check와
review thread 해소는 그대로 강제된다. 두 번째 push 권한자가 추가되면 다음
수렴에서 승인 수가 1로 올라가며 마지막 push 작성자는 그 승인을 할 수 없다.

### 배포 지시와 environment 승인

사용자가 특정 변경이나 릴리스에 대해 `배포`, `운영 반영`, `계속 진행`을
명시하면 그 지시는 해당 실행의 GitHub `production` environment review를 공식
API로 제출할 운영 권한까지 포함한다. 실행이 `pending_deployments`에 도달하면
저장소, run ID, environment ID가 정확히 그 지시 범위인지 열거하고, 구성된 인증
계정으로 `approved` review를 제출한 뒤 pending 해소와 보호 job 시작을 다시
읽는다. 같은 승인을 위해 사용자에게 재확인하지 않는다.

사용자 지시 자체를 GitHub 승인 기록으로 간주해서는 안 된다. 인증 계정이
eligible reviewer가 아니거나 API가 review를 거부하면 reviewer 규칙을 삭제하거나
우회하지 않고 응답을 보존해 기술적 blocker로 보고한다. 이 standing 권한은
지시받은 정확한 run에만 적용되며 다른 대기 실행을 함께 승인하지 않는다.

### frontend development QA OIDC (적용 전 readback 필수)

frontend의 same-artifact real-use는 기존 backend OIDC 역할을 공유하지 않고 별도
`academy-frontend-development-qa` 역할을 사용한다. trust는 aud=sts.amazonaws.com과
`repo:guswls3028-art/academy-frontend:ref:refs/heads/main` 하나만 허용한다.
PR/environment wildcard subject, 역할 체인, 장기 access key 또는 운영 계정 credential을
개발 job에 추가하지 않는다. frontend main의 job-level id-token:write만 사용한다.

read-only 계획은 `converge_frontend_development_qa.py --frontend-role-plan`과
`templates/iam/{trust,policy}_frontend_development_qa.json`, 고정 SSM Session/Port
document가 소유한다. 기존 개발 EC2의 parameter deny 보안 교정은 별도 계획/증거로
검토한다. `--frontend-role-plan`에는 Apply가 없다. 별도 `--apply-host-boundary`는
검토된 세 hash와 공용 잠금·readback 아래 기존 개발 host inline 정책에 두 Deny만
추가하며 frontend 역할/SSM 문서를 생성하지 않는다. 실제 IAM/SSM 변경은 검토한 exact
JSON과 소스/기존 정책 hash를 다시 확인한 뒤에만 수행한다. plan 또는 simulation 성공을
OIDC 인증/실제 development QA 성공으로 간주하지 않는다.

권한의 정확한 범위와 OpenDataChannel의 resource-level 제한 불가 및 session token
의존성은 [상시 개발 런타임](persistent-development-runtime.md)의 frontend 진입점이
소유한다. mandatory development job의 실제 non-skipped 성공과 cleanup0 없이는
frontend production environment 승인·배포를 시작하지 않는다. 기존 backend production
trust/permissions, 보호 규칙, rollback environment는 그대로 보존한다.

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
   뒤다. 백엔드 release deploy key와 Actions secret은 4단계 `-Apply`가 생성하며,
   개인키는 로컬 임시 파일에서 곧바로 secret store로 전달한 뒤 삭제한다.
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
   백엔드 AWS OIDC trust는 환경 없는 main job의 main-ref subject와 승인된
   production job의 environment subject만 허용해야 한다. 둘 중 하나가 빠지거나
   PR/tag/다른 environment subject가 추가되면 수렴 실패로 본다.
7. 배포 성공 후 Actions 설정의 SHA pinning, ruleset, environment reviewer,
   Dependabot security updates를 다시 읽어 증거에 기록한다.

ruleset이나 environment를 먼저 삭제하는 방식으로 복구하지 않는다. check 이름
변경이 필요하면 새 check가 성공하는 PR을 먼저 만든 뒤 ruleset required check를
같은 변경 창에서 교체한다.

## 5. 검증과 실패 처리

- `converge-github-governance.ps1` 기본 실행은 read-only이며 drift에서 실패한다.
- `-Apply`는 ruleset, Actions 기본 권한/SHA pinning, production/preview
  environment, vulnerability alerts, Dependabot security updates 및 백엔드 전용
  release deploy key/secret만 변경한다. 코드 push, merge, workflow 실행 승인은
  하지 않는다.
- production environment 승인 readback이 보이지 않으면 배포를 계속하지 않는다.
- frontend token 오류는 global key 복귀보다 token scope/account/project binding을
  먼저 교정한다.
- GitHub plan/API 제약으로 environment reviewer 또는 ruleset 저장이 거부되면
  적용 성공으로 간주하지 말고 해당 API 응답을 보존한다.

## 6. 관련 문서

- [deployment-modes.md](deployment-modes.md)
- [formal-deploy.md](formal-deploy.md)
- frontend `docs/DEPLOYMENT-OPERATIONS.md`
