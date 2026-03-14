# Front-Backend 연결 최종 완료 보고서

**생성일시:** 2026-03-06  
**상태:** FRONT_BACKEND_CONNECTED ✅

---

## 완료된 작업

### 1. alb.ps1 PowerShell 문법 수정
- `$sgId:` → `$($sgId):` (변수 확장 시 콜론 파싱 오류 수정)

### 2. 백엔드 배포
- `deploy.ps1 -MinimalDeploy -SkipNetprobe` 실행 완료
- API ASG instance-refresh 완료

### 3. Cloudflare api.hakwonplus.com → ALB
- DNS 레코드 A → CNAME 변경
- Target: `academy-v1-api-alb-1244943981.ap-northeast-2.elb.amazonaws.com`
- Proxied: true (HTTPS 종료)

### 4. 프론트엔드
- `.env.production`: `VITE_API_BASE_URL=https://api.hakwonplus.com`
- 빌드 후 Cloudflare Pages 배포 완료
- URL: https://b3054ee6.academy-frontend-26b.pages.dev

---

## 검증 결과

| 항목 | 결과 |
|------|------|
| https://api.hakwonplus.com/healthz | `{"status":"ok","service":"academy-api"}` |
| https://api.hakwonplus.com/api/v1/core/program/ | 200 (CORS Origin: academy-frontend-26b.pages.dev) |
| 프론트 로드 | 200 |
| End-to-end | ✅ 연결됨 |

---

## 배포 명령 요약

```powershell
# 백엔드
pwsh -File scripts/v1/deploy.ps1 -Env prod -MinimalDeploy -SkipNetprobe -AwsProfile default

# 프론트 (빌드 + Pages 배포)
cd academyfront; pnpm run build; wrangler pages deploy dist --project-name=academy-frontend
```

---

## 주요 URL

- **API**: https://api.hakwonplus.com
- **프론트**: https://academy-frontend-26b.pages.dev (또는 hakwonplus.com, limglish.kr 등)
- **ALB 직접**: http://academy-v1-api-alb-1244943981.ap-northeast-2.elb.amazonaws.com
