# 신규 테넌트 온보딩 — 커스텀 도메인

고객이 도메인·브랜드·대표 계정 정보를 전달한 시점부터 실제 로그인 검증까지의
정본 절차다. Cloudflare, 운영 DB, 백엔드, 프론트엔드 중 하나라도 빠지면 완료가
아니다.

## 원칙

- 테넌트 코드와 도메인을 추정하지 않는다.
- 운영 DB에서 ID 충돌을 먼저 확인하고 비어 있는 ID를 명시한다.
- 비밀번호는 채팅 외 문서·Git·셸 명령·감사 로그에 저장하지 않는다.
- 가비아 네임서버 위임 전에는 Pages CNAME을 만들지 않는다.
- 운영 DB에는 코드가 배포된 뒤 `provision_tenant`로 반영한다.
- 백엔드 배포는 격리 preproduction gate와 ASG 무중단 교체를 통과해야 한다.
- 같은 명령을 다시 실행해도 중복 생성되지 않아야 한다.

`setup_three_tenants`는 기존 테넌트 복구용 레거시 명령이다. 신규 온보딩 목록에
테넌트를 추가하지 않는다.

## 1. 입력 시트

작업 전에 아래 값을 확정한다. 비밀번호는 입력 시트에 적지 않는다.

| 값 | 예시 | 규칙 |
|---|---|---|
| 표시명 | 새봄수학 | 고객 화면에 표시할 정식 이름 |
| 코드 | saebom | 소문자 영문·숫자·하이픈 |
| 운영 ID | 10 | 운영 DB에서 비어 있는 양의 정수 |
| apex 도메인 | saebom.com | `www` 제외 |
| 대표자 표시명 | 홍길동 | 직원관리 대표 행 |
| 대표 로그인 ID | 별도 전달 | 문서·커밋에 기록하지 않음 |
| 초기 이용기간 | 30일 | 계약 또는 온보딩 결정값. 추정하지 않음 |
| 브랜드 색상 | `#123456`, `#fedcba` | 로고 실측 또는 고객 지정 |
| 로고 원본 | PNG/SVG | 고객 제공 원본 보존 |
| 고객 메모 | 요청 기능·운영 방식 | 비밀정보 제외 |

운영 ID와 기존 코드를 읽기 전용으로 확인한다.

```powershell
cd C:\academy\backend
.\scripts\v1\run-api-management-remote.ps1 -Command "check_tenants"
```

## 2. Phase 1 — Cloudflare zone과 네임서버

먼저 변경 대상을 확인하고 zone을 생성한다.

```powershell
cd C:\academy\backend
.\scripts\add-cloudflare-zone.ps1 -Domain "saebom.com" -WhatIf
.\scripts\add-cloudflare-zone.ps1 -Domain "saebom.com" -Confirm:$false
```

스크립트는 멱등형이다. 이미 zone이 있으면 새로 만들지 않고 해당 zone의
네임서버 1·2차를 다시 출력한다.

운영자가 가비아에 전달할 문구:

> 가비아 도메인 관리 → 네임서버 설정에서 가비아 기본 1·2·3차를 지우고,
> 전달드린 Cloudflare 1·2차만 입력해 주세요. IP 칸은 비워 두고 저장한 뒤
> “등록했다”라고 알려주세요.

### 수동 중단 게이트

가비아 저장 완료 확인 전에는 다음 작업을 실행하지 않는다.

- `pages-add-custom-domain.ps1` 실제 실행
- apex/`www` CNAME 생성
- 외부 HTTPS 성공 판정

코드와 로고 준비는 병행할 수 있지만, DNS 활성화로 오인해 완료 처리하지 않는다.

## 3. 코드·브랜딩 준비

### 백엔드

`apps/api/config/settings/prod.py`의 세 위치에 apex와 `www`를 추가한다.

- `ALLOWED_HOSTS`
- `CORS_ALLOWED_ORIGINS`
- `CSRF_TRUSTED_ORIGINS`

DB 프로비저닝 코드는 고객별 목록에 추가하지 않는다. 배포 후 범용 명령
`provision_tenant`를 사용한다.

### 프론트엔드

다음 경계를 모두 반영한다.

| 경계 | 파일 |
|---|---|
| ID·호스트·브랜드 레지스트리 | `src/shared/tenant/tenants/` |
| 로그인 테마 | `src/auth/themes/<code>.css`, `LoginPage.tsx` |
| 학생앱 테마 | `src/app_student/shared/ui/theme/tenants/`, `StudentLayout.tsx` |
| 성적표 색상 | `src/app_admin/domains/scores/utils/studentScoreReportTheme.ts` |
| PWA 아이콘 | `src/shared/pwa/tenantPwaMeta.ts` |
| 서버 렌더 OG·manifest·sitemap | `functions/[[path]].ts` |
| 정적 로고 | `public/tenants/<code>/` |

필수 정적 파일:

```text
logo.png
icon.png
favicon.png
og-image.png
apple-touch-icon.png
pwa-192.png
pwa-512.png
```

네이버 Search Advisor 코드는 발급된 경우에만 `functions/[[path]].ts`에
추가한다. 임의 값을 만들지 않는다.

### 로그인 브랜딩 게이트

로고를 흰색 공용 카드 위에 축소 배치하는 것만으로 브랜딩 완료 처리하지 않는다.
원본을 실제 크기로 열어 배경 유형과 시각 재료를 먼저 분류한다.

- 단색 배경 포함 로고: 페이지·브랜드 스테이지를 같은 색으로 이어 붙이거나
  원본 전체를 풀블리드 비주얼로 사용한다.
- 사진 배경 포함 로고: 사진을 브랜드 스테이지 전체에 사용하고 로그인 패널까지
  명도·채도·강조색을 이어 간다.
- 투명 로고: 고객 지정색 또는 로고 실측색으로 페이지 배경과 로그인 패널을
  별도로 설계한다.
- `primary_color` 하나만으로 화면을 끝내지 않는다. 배경·표면·본문·보조본문·
  강조·포커스의 4~6개 색상 토큰을 정한다.
- 장식은 브랜드 소재에서 가져온 한 가지 대표 장면만 사용한다. 입력·복구·
  회원가입 동작보다 장식이 앞서지 않게 한다.

전체 화면 구성이 필요한 브랜드는 `LoginPage.tsx`의 로그인 장면 정의와
`data-auth-part` 구조를 재사용하고, 실제 시각 결정은
`src/auth/themes/<code>.css`가 소유한다. 내부 테넌트 ID나 운영 용어는 화면 문구에
노출하지 않는다.

로컬 완료 조건:

- 데스크톱 1280×720 및 1366px에서 카드가 세로 스크롤 없이 보임
- 모바일 390×844에서 한 열로 접히고 가로 스크롤이 없음
- 로그인 폼 하단과 약관 푸터가 겹치지 않음
- 키보드 포커스가 브랜드 강조색으로 식별됨
- `prefers-reduced-motion`에서 필수 정보가 애니메이션에 의존하지 않음
- DOM에서 올바른 `data-tenant`, 제목, 로그인 폼을 확인
- 스크린샷을 직접 검토해 로고 배경 경계와 문구 겹침이 없음

## 4. Phase 2 — 가비아 위임 확인 후

공용 DNS에서 Cloudflare 네임서버가 보이는지 확인한다.

```powershell
Resolve-DnsName -Type NS saebom.com -Server 1.1.1.1
Resolve-DnsName -Type NS saebom.com -Server 8.8.8.8
```

두 조회 모두 Phase 1에서 발급한 값이어야 한다. 가비아 기본
`ns.gabia.*`가 보이면 전파를 기다리고 중단한다.

## 5. 배포

백엔드와 프론트엔드는 각각 독립 저장소의 정식 배포 경로를 사용한다.

- 백엔드: `.github/workflows/v1-build-and-push-latest.yml`
  - immutable digest 후보
  - 격리 preproduction DB/health 검증
  - 운영 migration
  - ASG/ALB health-gated rolling refresh
  - 배포 후 digest·worker·queue·health 확인
- 프론트엔드: `.github/workflows/quality-gate.yml`
  - typecheck/lint/build/E2E gate
  - GitHub Actions가 Cloudflare Pages의 단일 production deploy owner인지 확인

배포된 revision에 신규 도메인 설정과 `provision_tenant` 명령이 포함됐는지
확인한 뒤 운영 DB를 변경한다.

## 6. 운영 DB 프로비저닝

먼저 owner 없이 dry-run을 실행한다. 표시명에 공백이 있으면 따옴표를 유지한다.

```powershell
cd C:\academy\backend
.\scripts\v1\run-api-management-remote.ps1 -Command 'provision_tenant saebom --tenant-id 10 --name "새봄수학" --domain saebom.com --login-title "새봄수학" --login-subtitle "saebom.com" --window-title "새봄수학" --logo-url /tenants/saebom/logo.png --primary-color "#123456" --dry-run'
```

출력의 코드·ID·primary domain이 입력 시트와 정확히 같을 때만 실제 실행한다.

```powershell
.\scripts\v1\run-api-management-remote.ps1 -Command 'provision_tenant saebom --tenant-id 10 --name "새봄수학" --domain saebom.com --login-title "새봄수학" --login-subtitle "saebom.com" --window-title "새봄수학" --logo-url /tenants/saebom/logo.png --primary-color "#123456"'
```

대표 계정은 HTTPS 개발자 콘솔의 테넌트 상세 → Owner 등록에서 만든다. 비밀번호를
셸 인수로 전달하지 않는다. 신규 owner는 첫 로그인 후 비밀번호 변경이 강제되며,
감사 로그에는 `password_changed=true`만 기록되고 원문은 저장되지 않는다.

Program 생성 직후에는 이용기간이 비어 있어 로그인 화면에 이용 연장 안내가 뜬다.
입력 시트에서 확정한 기간으로 공식 구독 명령을 dry-run한 뒤 적용한다. 아래 30일은
예시이며 계약값을 대신하지 않는다.

```powershell
.\scripts\v1\run-api-management-remote.ps1 -Command 'extend_subscription --tenant saebom --days 30 --confirm-live --dry-run'
.\scripts\v1\run-api-management-remote.ps1 -Command 'extend_subscription --tenant saebom --days 30 --confirm-live'
```

적용 결과에서 `subscription_expires_at`과 `next_billing_at`이 함께 설정되고,
로그인 화면의 이용 연장 안내가 사라졌는지 확인한다.

## 7. Pages·DNS 활성화

DB와 양쪽 배포가 준비된 뒤 실행한다.

```powershell
cd C:\academy\backend
.\scripts\pages-add-custom-domain.ps1 -Domain "saebom.com" -WhatIf
.\scripts\pages-add-custom-domain.ps1 -Domain "saebom.com" -Confirm:$false
```

스크립트가 수행하는 일:

1. Pages 프로젝트 `academy-frontend`에 apex와 `www` 등록
2. 잘못된 apex/`www` CNAME만 제거
3. 올바른 proxied CNAME은 유지
4. 누락된 레코드를 `academy-frontend-26b.pages.dev`로 생성

## 8. 완료 검증

```powershell
Resolve-DnsName -Type NS saebom.com -Server 1.1.1.1
curl.exe -sS -o NUL -w "%{http_code}`n" https://saebom.com/login
curl.exe -sS -o NUL -w "%{http_code}`n" https://www.saebom.com/login
```

다음 항목을 모두 확인한다.

- apex와 `www`가 HTTP 200
- 인증서 정상
- 제목·favicon·OG·PWA manifest가 해당 브랜드
- 데스크톱 1366px·모바일 390px 로그인 화면
- 로고가 페이지 배경·로그인 패널과 시각적으로 이어지고 흰 공용 카드에 고립되지 않음
- 대표 계정 로그인과 최초 비밀번호 변경
- 로그인 후 역할이 owner
- 새 테넌트의 학생·강의·성적 목록이 비어 있음
- 다른 테넌트 데이터가 보이지 않음
- 백엔드 `/healthz`, `/health` 정상

프론트 가용성 스크립트로 신규 URL만 좁게 재검증할 수 있다.

```powershell
cd C:\academy\frontend
$env:TENANT_AVAILABILITY_URLS = "https://saebom.com/login,https://www.saebom.com/login"
pnpm verify:tenant-availability
Remove-Item Env:TENANT_AVAILABILITY_URLS
```

## 9. 실패 시 중단·복구

| 증상 | 확인·조치 |
|---|---|
| NS가 가비아 값 | 가비아 저장/전파 대기. Pages 실제 실행 중단 |
| Cloudflare 1014/1016 | `get-zone-dns.ps1`로 apex/`www`와 Pages target 확인 |
| CORS/CSRF 오류 | 배포된 backend revision과 `prod.py` 두 origin 확인 |
| tenant required/404 | 운영 DB의 `TenantDomain` apex/`www`와 활성 상태 확인 |
| 다른 학원 브랜딩 | frontend registry hostname/code/ID 확인 |
| 다른 학원 데이터 노출 | 즉시 도메인 비활성화 후 tenant isolation incident로 처리 |

관련 스크립트:

| 용도 | 경로 |
|---|---|
| zone 생성·NS 재조회 | `scripts/add-cloudflare-zone.ps1` |
| Cloudflare 전체 NS 조회 | `scripts/get-cloudflare-nameservers.ps1` |
| Pages·CNAME 활성화 | `scripts/pages-add-custom-domain.ps1` |
| zone 레코드 조회 | `scripts/get-zone-dns.ps1` |
| DB 범용 프로비저닝 | `python manage.py provision_tenant` |
| 운영 Django 명령 | `scripts/v1/run-api-management-remote.ps1` |
