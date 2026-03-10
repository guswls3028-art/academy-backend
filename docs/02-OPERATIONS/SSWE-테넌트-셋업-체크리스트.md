# SSWE 테넌트(sswe.co.kr) 실제 이용 가능 체크리스트

## 완료된 항목

- [x] **Cloudflare zone** — sswe.co.kr zone 추가됨 (API)
- [x] **가비아 네임서버** — 사용자가 저장 완료 (malcolm.ns.cloudflare.com, zita.ns.cloudflare.com)
- [x] **Cloudflare DNS** — sswe.co.kr / www.sswe.co.kr → `academy-frontend.pages.dev` CNAME 추가(프록시 ON)
- [x] **백엔드 설정** — `setup_three_tenants.py`에 sswe 추가, `prod.py`에 ALLOWED_HOSTS/CORS/CSRF 반영
- [x] **프론트엔드** — TenantId 5, sswe 테넌트 정의, 로그인 라우트, `[[path]].ts` OG/타이틀, 학생앱 테마/로고 목록

## 서버에서 한 번만 실행할 것 (DB 반영)

백엔드가 **배포된 서버** 또는 로컬에서 **실제 DB**에 sswe 테넌트·도메인·Program을 넣어야 합니다.

```bash
cd backend
python manage.py setup_three_tenants
```

이 명령으로 sswe Tenant, TenantDomain(sswe.co.kr, www.sswe.co.kr), Program이 생성/연결됩니다.  
**이미 실행했다면** 기존 테넌트는 유지되고 sswe만 추가됩니다.

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
2. **백엔드 DB** — 위 `setup_three_tenants` 실행.
3. **프론트 배포** — sswe 반영된 프론트를 Cloudflare Pages에 배포(빌드/푸시).
4. **접속** — https://sswe.co.kr 또는 https://www.sswe.co.kr 접속 후 로그인 동작 확인.

이 체크리스트까지 하면 sswe.co.kr 실제 이용 가능 상태입니다.
