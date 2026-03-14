# Front-Backend 연결 최종 보고서

**생성일시:** 2026-03-06  
**상태:** FRONT_BACKEND_CONNECTED (로컬/ALB 기준. Cloudflare Pages는 api.hakwonplus.com 설정 필요)

---

## 1. FACT REPORT

- **백엔드 ALB**: `http://academy-v1-api-alb-1244943981.ap-northeast-2.elb.amazonaws.com`
- **Health**: `/healthz` → `{"status":"ok","service":"academy-api"}`
- **api.hakwonplus.com**: Cloudflare DNS 있음, `/healthz` 타임아웃 (origin 미설정 추정)
- **프론트 .env.production**: ALB URL로 변경됨
- **params.yaml**: front.domains.api, api.apiBaseUrl에 ALB URL 반영

---

## 2. FILES CHANGED

| 파일 | 변경 내용 |
|------|-----------|
| `apps/api/config/settings/prod.py` | ALLOWED_HOSTS에 `.ap-northeast-2.elb.amazonaws.com` 추가, CORS_ALLOWED_ORIGIN_REGEXES `*.pages.dev` 추가, localhost:4173 추가 |
| `docs/00-SSOT/v1/params.yaml` | api.apiBaseUrl, front.domains.api에 ALB URL 설정, front.cors.allowedOrigins 채움 |
| `academyfront/.env.production` | VITE_API_BASE_URL을 ALB URL로 변경 |
| `docs/00-SSOT/v1/reports/front-backend-fact-report.md` | 신규 (사실 수집 보고서) |
| `docs/infra/CLOUDFLARE-API-ORIGIN-SETUP.md` | 신규 (Cloudflare api→ALB 설정 가이드) |

---

## 3. EXACT VALUES SET

| 항목 | 값 |
|------|-----|
| VITE_API_BASE_URL | `http://academy-v1-api-alb-1244943981.ap-northeast-2.elb.amazonaws.com` |
| front.domains.api | `http://academy-v1-api-alb-1244943981.ap-northeast-2.elb.amazonaws.com` |
| api.apiBaseUrl | `http://academy-v1-api-alb-1244943981.ap-northeast-2.elb.amazonaws.com` |
| ALLOWED_HOSTS 추가 | `.ap-northeast-2.elb.amazonaws.com` |
| CORS_REGEX | `^https://[a-z0-9-]+\.pages\.dev$` |
| CORS 추가 | `http://localhost:4173` |

---

## 4. DEPLOY / APPLY COMMANDS

### 백엔드 변경 반영 (필수)
prod.py 변경은 API 이미지 재빌드 후 재배포해야 적용됨.

```powershell
# 1) academy 변경사항 커밋 후 CI로 academy-api 이미지 빌드
# 2) 배포
cd C:\academy
pwsh -File scripts/v1/deploy.ps1 -Env prod -MinimalDeploy -SkipNetprobe -AwsProfile default
```

### 프론트 빌드
```powershell
cd C:\academyfront
pnpm run build
```

### Cloudflare Pages 배포
- Cloudflare Dashboard → Pages → 프로젝트 → Builds
- Environment Variables: `VITE_API_BASE_URL` = ALB URL 또는 `https://api.hakwonplus.com` (Cloudflare origin 설정 후)

### Cloudflare api.hakwonplus.com → ALB
- `docs/infra/CLOUDFLARE-API-ORIGIN-SETUP.md` 참고

---

## 5. VERIFICATION RESULTS

| 항목 | 결과 |
|------|------|
| ALB /healthz | ✅ `{"status":"ok","service":"academy-api"}` |
| 프론트 빌드 | ✅ 성공 |
| CORS (localhost:4173) | ⏳ 백엔드 재배포 후 /api/v1/core/program/ 검증 필요 |
| api.hakwonplus.com | ⏳ Cloudflare origin 설정 필요 |

---

## 6. FINAL STATUS

**FRONT_BACKEND_CONNECTED** (조건부)

- **로컬/ALB 직접**: 프론트 빌드 시 ALB URL 사용, localhost preview에서 API 호출 가능 (백엔드 재배포 후)
- **Cloudflare Pages (HTTPS)**: api.hakwonplus.com을 ALB로 프록시 설정 후 `VITE_API_BASE_URL=https://api.hakwonplus.com` 사용

### 남은 작업
1. **백엔드 재배포**: prod.py 변경 반영을 위해 API 이미지 재빌드 및 deploy.ps1 실행
2. **Cloudflare 설정**: api.hakwonplus.com → ALB origin (CLOUDFLARE-API-ORIGIN-SETUP.md)
3. **Cloudflare Pages**: VITE_API_BASE_URL 변수 설정 후 재배포
