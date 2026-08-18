# 신과함께 — 온보딩 메모

**기준일:** 2026-08-18 KST

**상태:** Cloudflare zone·가비아 위임·정식 배포·운영 DB 프로비저닝 완료 · 구독 입력 대기

**운영 도메인:** `godmin.kr`

**테넌트:** ID `11`, code `godmin`

## 확정 입력

- 정식 표시명: `신과함께`
- apex / www: `godmin.kr` / `www.godmin.kr`
- 로고: 고객 제공 투명 PNG 워드마크
- 브랜드 팔레트: 차콜 `#383838`, 코어 민트 `#35c7a0`, 딥 민트 `#147a62`,
  옅은 민트 `#e4f7ef`
- 대표 로그인 정보와 비밀번호는 문서·Git·셸 명령에 기록하지 않는다.
- 초기 이용기간은 계약값이 확정되기 전까지 임의로 적용하지 않는다.

## 브랜드 기준

- 제공 원본은 `logo.png`로 비율과 문구를 보존한다.
- 헤더 아이콘은 원본 첫 글자 `m`을 1:1 투명 캔버스에 파생해 32px에서도
  식별되게 한다.
- 로그인은 흑연색 워드마크와 민트 윤곽을 본뜬 두 개의 원형 궤도를 한 장면으로
  사용한다. 입력·복구·회원가입보다 장식이 앞서지 않으며 reduced motion을 따른다.
- 학생앱과 성적표는 차콜을 기준색, 민트를 포커스·현재 위치·상태 강조로 사용한다.
- 프런트 세부 계약은 `frontend/docs/TENANT-BRANDING.md`의 `godmin` 항목이 소유한다.

## 진행 상태

- [x] **G0 입력 확정(계정·기간 제외)** — 표시명·코드·ID·도메인·로고·팔레트
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
- [ ] **G5 운영 DB·구독** — `provision_tenant` dry-run 뒤 ID `11`, code `godmin`,
  apex `godmin.kr`, owner 미요청을 확인해 적용했다. 재조회에서 ACTIVE 테넌트와
  apex/`www` 도메인·Program 생성을 확인했다. 계약 이용기간이 확정되기 전까지
  구독은 적용하지 않는다.
- [ ] **G6 Pages·HTTPS** — apex/`www` Pages·CNAME과 HTTP 200
- [ ] **G7 대표 계정** — 소유자 0명 확인 후 개발자 콘솔에서 1회 생성
- [ ] **G8 실제 인계** — 최초 비밀번호 변경과 role·tenant isolation 확인

## 현재 발급된 네임서버

```text
1차: barbara.ns.cloudflare.com
2차: thaddeus.ns.cloudflare.com
```

가비아에서는 기존 네임서버를 제거하고 위 두 호스트만 1차·2차에 입력한다. IP
필드는 비워 둔다. 2026-08-18 KST에 `.kr` 권위 DNS와 Cloudflare·Google 공용
DNS에서 위 두 값의 위임을 확인했다. Pages custom domain과 apex/`www` CNAME은
G5 운영 DB·구독 적용 뒤 G6 절차에서 활성화한다.
