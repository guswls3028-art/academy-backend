# 신과함께 — 온보딩 메모

**기준일:** 2026-08-18 KST

**상태:** Cloudflare zone·가비아 위임·정식 배포·운영 DB·무기한 이용·Pages HTTPS 완료 · 대표 계정 생성 대기

**운영 도메인:** `godmin.kr`

**테넌트:** ID `11`, code `godmin`

## 확정 입력

- 정식 표시명: `신과함께`
- apex / www: `godmin.kr` / `www.godmin.kr`
- 로고: 고객 제공 투명 PNG 워드마크
- 브랜드 팔레트: 차콜 `#383838`, 코어 민트 `#35c7a0`, 딥 민트 `#147a62`,
  옅은 민트 `#e4f7ef`
- 대표 로그인 정보와 비밀번호는 전달받았지만 문서·Git·셸 명령에 기록하지 않는다.
- 이용 정책: 명시적으로 승인된 무기한 과금 제외. 만료일과 다음 결제일을 만들지 않는다.
- 대표 계정은 인증된 개발자 콘솔에서 기존 소유자 0명을 확인한 뒤 1회만 만든다.

## 브랜드 기준

- 제공 원본은 `logo.png`로 비율과 문구를 보존한다.
- 헤더 아이콘은 원본 첫 글자 `m`을 1:1 투명 캔버스에 파생해 32px에서도
  식별되게 한다.
- 로그인은 흑연색 워드마크와 민트 윤곽을 본뜬 두 개의 원형 궤도를 한 장면으로
  사용한다. 입력·복구·회원가입보다 장식이 앞서지 않으며 reduced motion을 따른다.
- 학생앱과 성적표는 차콜을 기준색, 민트를 포커스·현재 위치·상태 강조로 사용한다.
- 프런트 세부 계약은 `frontend/docs/TENANT-BRANDING.md`의 `godmin` 항목이 소유한다.

## 진행 상태

- [x] **G0 입력 확정** — 표시명·코드·ID·도메인·로고·팔레트·대표 정보·이용 정책
- [x] **G1 충돌 확인** — 운영 DB에 ID `11`과 code `godmin`이 비어 있음을 확인
- [x] **G2 Cloudflare 준비** — zone 생성과 NS 1·2차 발급
- [x] **G3 코드·브랜딩 준비** — backend host/origin, registry·로그인·헤더·학생앱·
  성적표·OG/PWA 반영. Django check/migration check, provision 4 tests, frontend
  typecheck/lint/build, PWA 9 tests, 1366×768·390×844 시각 검증 통과
- [x] **G4 위임·정식 배포** — `.kr` 권위 DNS와 `1.1.1.1`·`8.8.8.8` 위임 확인.
  backend `d2f3a397bca0b31801eb2f4aa8b751011aeacf1c`는 run `32128046410`에서
  격리 개발·사전운영·운영 ASG 교체와 최종 검증을 통과했다. frontend
  `bf3f59ded7c30f9dbb6c81a9a08bdaa0e9dc5f6c`는 run `32128046080`에서 후보
  preview·Pages 운영 배포·왕복 E2E를 통과했고 운영 `version.json`과 일치한다.
  API `/healthz`·`/health` 및 프런트 `/login` HTTP 200 readback 완료
- [x] **G5 운영 DB·이용 정책** — `provision_tenant` dry-run 뒤 ID `11`, code
  `godmin`, apex `godmin.kr`, owner 미요청을 확인해 적용했다. 운영 런타임의
  `BILLING_EXEMPT_TENANT_IDS`에 ID `11`을 기존 값 보존 방식으로 추가하고 API
  ASG 무중단 교체를 완료했다. 런타임 재조회에서 Program `active`, 만료일·다음
  결제일 `NULL`, `is_subscription_active=True`를 확인했고
  `audit_billing_fields --tenant godmin --strict`은 문제 0건이다.
- [x] **G6 Pages·HTTPS** — apex/`www`를 `academy-frontend-26b.pages.dev`에
  연결하고 두 Pages custom domain의 `active` 전환을 확인했다. 두 `/login`은
  HTTPS 200이며 title·OG title·OG site name `신과함께`, favicon 200이다.
  frontend revision `d35381f51fdfda8a2632a3447a7596016b1cc353`을 두 호스트에서
  재조회했고 1440×900과 390×844에서 로고·입력·버튼·오버플로·메타데이터를
  검증했다.
- [ ] **G7 대표 계정** — 대표 정보는 전달받았고 운영 소유자 수는 여전히
  0명이다. 인증된 개발자 콘솔에서 중복을 확인한 뒤 1회만 생성한다.
- [ ] **G8 실제 인계** — 최초 비밀번호 변경과 role·tenant isolation 확인

## 현재 발급된 네임서버

```text
1차: barbara.ns.cloudflare.com
2차: thaddeus.ns.cloudflare.com
```

가비아에서는 기존 네임서버를 제거하고 위 두 호스트만 1차·2차에 입력한다. IP
필드는 비워 둔다. 2026-08-18 KST에 `.kr` 권위 DNS와 Cloudflare·Google 공용
DNS에서 위 두 값의 위임을 확인했다. 같은 날 Pages custom domain과
apex/`www` 프록시 CNAME을 활성화하고 두 HTTPS 호스트의 200 응답을 확인했다.
