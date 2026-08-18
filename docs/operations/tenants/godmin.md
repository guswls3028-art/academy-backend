# 신과함께 — 온보딩 메모

**기준일:** 2026-08-18 KST

**상태:** Cloudflare zone·네임서버 발급 완료 · 코드·브랜딩 로컬 검증 완료 · 가비아 위임 대기

**운영 도메인:** `godmin.kr`

**테넌트:** ID `11`, code `godmin`

## 확정 입력

- 정식 표시명: `신과함께`
- apex / www: `godmin.kr` / `www.godmin.kr`
- 로고: 고객 제공 투명 PNG 워드마크
- 브랜드 팔레트: 차콜 `#383838`, 세이지 `#b0d0a0`, 미스트 `#f3f7f0`
- 대표 로그인 정보와 비밀번호는 문서·Git·셸 명령에 기록하지 않는다.
- 초기 이용기간은 계약값이 확정되기 전까지 임의로 적용하지 않는다.

## 브랜드 기준

- 제공 원본은 `logo.png`로 비율과 문구를 보존한다.
- 헤더 아이콘은 원본 첫 글자 `m`을 1:1 투명 캔버스에 파생해 32px에서도
  식별되게 한다.
- 로그인은 흑연색 워드마크와 세이지 윤곽을 본뜬 두 개의 원형 궤도를 한 장면으로
  사용한다. 입력·복구·회원가입보다 장식이 앞서지 않으며 reduced motion을 따른다.
- 학생앱과 성적표는 차콜을 주색, 세이지를 포커스·상태 강조로 사용한다.
- 프런트 세부 계약은 `frontend/docs/TENANT-BRANDING.md`의 `godmin` 항목이 소유한다.

## 진행 상태

- [x] **G0 입력 확정(계정·기간 제외)** — 표시명·코드·ID·도메인·로고·팔레트
- [x] **G1 충돌 확인** — 운영 DB에 ID `11`과 code `godmin`이 비어 있음을 확인
- [x] **G2 Cloudflare 준비** — zone 생성과 NS 1·2차 발급
- [x] **G3 코드·브랜딩 준비** — backend host/origin, registry·로그인·헤더·학생앱·
  성적표·OG/PWA 반영. Django check/migration check, provision 4 tests, frontend
  typecheck/lint/build, PWA 9 tests, 1366×768·390×844 시각 검증 통과
- [ ] **G4 위임·정식 배포** — 공용 DNS와 backend/frontend 정식 배포
- [ ] **G5 운영 DB·구독** — provision dry-run·적용, 계약 기간 적용
- [ ] **G6 Pages·HTTPS** — apex/`www` Pages·CNAME과 HTTP 200
- [ ] **G7 대표 계정** — 소유자 0명 확인 후 개발자 콘솔에서 1회 생성
- [ ] **G8 실제 인계** — 최초 비밀번호 변경과 role·tenant isolation 확인

## 현재 발급된 네임서버

```text
1차: barbara.ns.cloudflare.com
2차: thaddeus.ns.cloudflare.com
```

가비아에서는 기존 네임서버를 제거하고 위 두 호스트만 1차·2차에 입력한다. IP
필드는 비워 둔다. 공용 DNS에서 두 값이 확인되기 전에는 Pages custom domain과
apex/`www` CNAME을 활성화하지 않는다.
