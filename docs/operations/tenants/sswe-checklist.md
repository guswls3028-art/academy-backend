# SSWE 테넌트(sswe.co.kr) 실제 이용 가능 체크리스트

> **역사 기록 — 신규 온보딩에 재사용 금지.** 이 문서의 ID·Pages target·
> `setup_three_tenants` 절차는 SSWE 당시 상태를 기록한 것이다. 신규 테넌트는
> [custom-domain.md](custom-domain.md)와
> [onboarding-run-sheet.md](onboarding-run-sheet.md)의 G0~G9만 사용하고 DB는
> `provision_tenant`, 최종 판정은 `audit_tenant_onboarding`으로 수행한다.

## 완료된 항목

- [x] **Cloudflare zone** — sswe.co.kr zone 추가됨 (API)
- [x] **가비아 네임서버** — 사용자가 저장 완료 (malcolm.ns.cloudflare.com, zita.ns.cloudflare.com)
- [x] **Cloudflare DNS** — sswe.co.kr / www.sswe.co.kr → `academy-frontend.pages.dev` CNAME 추가(프록시 ON)
- [x] **백엔드 설정** — `setup_three_tenants.py`에 sswe 추가, `prod.py`에 ALLOWED_HOSTS/CORS/CSRF 반영
- [x] **프론트엔드** — TenantId 5, sswe 테넌트 정의, 로그인 라우트, `[[path]].ts` OG/타이틀, 학생앱 테마/로고 목록

## 당시 서버 반영 기록 (재실행 금지)

당시에는 레거시 복구 명령이 SSWE를 포함했다. 현재 신규 온보딩에는 아래 명령을
사용하지 않는다.

```bash
cd backend
python manage.py setup_three_tenants
```

현재 SSWE 상태를 진단할 때도 이 명령을 재실행하지 않고 읽기 전용 DB 조회와
현재 정본의 감사 명령을 사용한다.

## Cloudflare Pages 커스텀 도메인 (필요 시)

프론트가 **다른 프로젝트 서브도메인**(예: `academy-frontend-26b.pages.dev`)을 쓰는 경우:

1. **방법 A** — CNAME 타깃 변경 후 DNS만 사용  
   ```powershell
   .\scripts\add-cloudflare-zone-dns.ps1 -PagesTarget "academy-frontend-26b.pages.dev"
   ```
   (기존 @/www 레코드가 있으면 대시보드에서 수동 수정)

2. **방법 B** — Cloudflare 대시보드에서 Pages 프로젝트 → **Custom domains** → **Add** → `sswe.co.kr`, `www.sswe.co.kr` 추가  
   (CNAME이 이미 있으면 검증 후 활성화됨)

## 확인 순서

1. **DNS 전파** — 가비아 NS 저장 후 수 분~최대 48시간. `nslookup sswe.co.kr` 로 NS가 Cloudflare로 나오면 OK.
2. **백엔드 DB** — 당시 `setup_three_tenants`로 반영했으며 현재는 재실행 금지.
3. **프론트 배포** — sswe 반영된 프론트를 Cloudflare Pages에 배포(빌드/푸시).
4. **접속** — https://sswe.co.kr 또는 https://www.sswe.co.kr 접속 후 로그인 동작 확인.

이 체크리스트까지 하면 sswe.co.kr 실제 이용 가능 상태입니다.
