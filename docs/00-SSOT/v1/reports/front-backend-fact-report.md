# Front-Backend 연결 FACT REPORT

**생성일시:** 2026-03-06  
**목적:** 현재 배포된 AWS 백엔드와 프론트/Cloudflare/R2 설정 정렬

---

## A. Backend Truth (AWS + Repo)

### 실제 백엔드 진입점
| 항목 | 값 |
|------|-----|
| ALB DNS | `academy-v1-api-alb-1244943981.ap-northeast-2.elb.amazonaws.com` |
| Scheme | internet-facing, HTTP 80 |
| Health endpoint | `http://ALB_DNS/healthz` → `{"status":"ok","service":"academy-api"}` |
| Route53/Custom domain | params.yaml `api.apiBaseUrl` 비어있음. prod.py `API_BASE_URL` = `https://api.hakwonplus.com` |

### api.hakwonplus.com 상태
- DNS: Cloudflare IP (104.21.12.155, 172.67.132.46)
- `/healthz` 호출: 타임아웃 (연결 불가 또는 origin 미설정)

### 백엔드 CORS/ALLOWED_HOSTS (prod.py)
- **ALLOWED_HOSTS**: api.hakwonplus.com, academy-frontend.pages.dev, localhost, 127.0.0.1, .ap-northeast-2.compute.internal 등
- **ALB hostname 미포함**: `*.elb.amazonaws.com` 없음 → ALB 직접 접근 시 400 가능
- **CORS_ALLOWED_ORIGINS**: https://academy-frontend.pages.dev, https://hakwonplus.com, localhost:5174 등
- **CORS**: credentials=true, 와일드카드 비허용

### 백엔드 SSM /academy/api/env
- CORS/ALLOWED_HOSTS 오버라이드 없음 (코드 prod.py 고정값 사용)
- R2_*, CDN_HLS_*, DB, REDIS 등 런타임 env 존재

---

## B. Frontend Truth (academyfront)

### API Base URL
| 파일 | 값 |
|------|-----|
| .env.production | `VITE_API_BASE_URL=https://api.hakwonplus.com` |
| .env.example | `VITE_API_BASE_URL=https://api.hakwonplus.com` |
| axios.ts | `baseURL: ${VITE_API_BASE_URL}/api/v1` |

### Media/CDN
| 파일 | 값 |
|------|-----|
| .env.production | `VITE_MEDIA_CDN_BASE=https://cdn.hakwonplus.com` |
| VideoThumbnail.tsx | `VITE_MEDIA_CDN_BASE` 사용 |

### Cloudflare Pages
- wrangler.toml/wrangler.json: 없음 (Pages는 대시보드/CI 설정)
- package.json: `pnpm run build` → dist/
- REFERENCE.md: Build `pnpm run build`, Output `dist`, VITE_API_BASE_URL 필수

### 불일치
1. **프론트 API URL**: api.hakwonplus.com 사용 중 → 현재 응답 없음. ALB가 실제 백엔드.
2. **백엔드 ALLOWED_HOSTS**: ALB hostname 없음 → ALB 직접 접근 시 Host 검증 실패 가능.

---

## C. Cloudflare Truth

### 확인된 사항
- api.hakwonplus.com: Cloudflare DNS (프록시 가능)
- academy-frontend.pages.dev: 404 (프로젝트명/경로 상이 가능)
- REFERENCE: academy-frontend-26b.pages.dev 언급
- R2 버킷: academy-admin, academy-ai, academy-excel, academy-storage, academy-video (cursorrules)

### 미확인 (외부 설정)
- api.hakwonplus.com Cloudflare Origin (ALB 가리키는지)
- Pages 프로젝트 실제 URL
- cdn.hakwonplus.com → R2 바인딩

---

## D. 정렬을 위해 필요한 변경

### 1. 백엔드 (academy)
- `prod.py` ALLOWED_HOSTS에 `".ap-northeast-2.elb.amazonaws.com"` 추가 (ALB 직접 접근 허용)

### 2. 프론트 (academyfront)
- `.env.production`: `VITE_API_BASE_URL`를 현재 ALB URL로 변경 (api.hakwonplus.com 미동작 시)
- 또는 Cloudflare에서 api.hakwonplus.com → ALB origin 설정 후 기존 URL 유지

### 3. SSOT (params.yaml)
- `front.domains.api`: 현재 ALB URL 또는 api.hakwonplus.com 명시
- `api.apiBaseUrl`: 배포 시 ALB URL로 설정 가능하도록

### 4. Cloudflare (수동)
- api.hakwonplus.com DNS: A/CNAME → ALB DNS (또는 Cloudflare Proxy → ALB origin)
- Pages: VITE_API_BASE_URL 변수로 ALB 또는 api.hakwonplus.com 설정
