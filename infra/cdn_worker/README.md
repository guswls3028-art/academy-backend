# Academy CDN Video Worker — Deployment Runbook

> **Status**: 코드 + 정합성 검증 완료. 라이브 배포 대기 (CF Dashboard 또는 wrangler login 필요).
>
> **목적**: R2 public bucket 노출 차단. 백엔드(`cloudflare_signing.py`)가 생성한 HMAC 서명을 본 Worker 가 검증한 뒤 R2 private bucket 에서 fetch 해 응답.

## 정합성 검증 (이미 PASS)
- `node infra/cdn_worker/test/verify-parity.mjs` → 4/4 PASS
- Python backend `cloudflare_signing.py` ↔ JS test ↔ Worker WebCrypto API 3-way byte-perfect 일치 확인

## 배포 절차 (예상 30분, 모두 reversible)

### 0. 사전 확인
```bash
wrangler --version  # 4.70+ 권장 (이미 설치됨)
node --version       # 20+ (이미 설치됨)
```

### 1. Cloudflare 로그인 (one-time)
**옵션 A**: 브라우저 OAuth
```bash
cd infra/cdn_worker
wrangler login
# 브라우저 열림 → Cloudflare 계정 (hakwonplus.com 관리 계정) 로그인 → Authorize
```

**옵션 B**: API Token (CI/CD 용)
- Cloudflare Dashboard → My Profile → API Tokens → Create Token
- Template: "Edit Cloudflare Workers" + R2 read/write 권한 추가
- Token 을 `c:/academy/.secrets/cf-api-token.txt` 에 저장 (gitignored)
- 환경변수: `$env:CLOUDFLARE_API_TOKEN = Get-Content c:/academy/.secrets/cf-api-token.txt`

### 2. Secret 등록 (현재 dev 값 그대로 사용. 진짜 회전은 단계 5)
```bash
cd infra/cdn_worker
# 백엔드 SSM /academy/api/env 의 CDN_HLS_SIGNING_SECRET 과 동일하게 (현재 'dev-signing-secret')
wrangler secret put CDN_HLS_SIGNING_SECRET
# prompt → dev-signing-secret 붙여넣기
```

### 3. Worker 첫 배포 (Route 없이 stage 만)
```bash
wrangler deploy
# 결과: academy-cdn-video.<account>.workers.dev 에 배포됨
# 이 URL 로 직접 테스트 (DNS 미사용)
```

### 4. Worker 단독 테스트 (DNS 미연결, R2 미private)
```bash
# 서명된 URL 생성 (백엔드 PROD 에서 실제 시청 시 받는 URL 형식)
# 임시로 Worker 단독 URL 에 같은 path + query 로 시도
SIGNED_URL='https://academy-cdn-video.<account>.workers.dev/tenants/1/video/hls/284/master.m3u8?exp=...&sig=...&kid=v1&uid=12'
curl -I "$SIGNED_URL"  # 기대: 200
curl -I "${SIGNED_URL/sig=*/sig=tamper}"  # 기대: 403
curl -I "https://academy-cdn-video.<account>.workers.dev/tenants/1/video/hls/284/master.m3u8"  # 기대: 401 (no sig)
```

### 5. Route + DNS 연결 (cdn.hakwonplus.com)
**Cloudflare Dashboard**:
1. Workers & Pages → academy-cdn-video → Settings → Triggers → Add Route
2. Pattern: `cdn.hakwonplus.com/tenants/*` (또는 `cdn.hakwonplus.com/*`)
3. Zone: `hakwonplus.com`

DNS 가 이미 Cloudflare 통과 중이므로 (`104.21.x.x` 확인됨) 별도 CNAME 불필요. Route 추가만으로 발효.

### 6. SECRET_KEY 회전 (선택 — 추천)
백엔드 + Worker 동시 갱신:
```bash
# 1) 새 64-char SECRET 생성
NEW_CDN_SK=$(openssl rand -hex 32)

# 2) 백엔드 SSM 업데이트 (scripts/v1/core/ssm-safe-update.ps1 helper 사용)
pwsh -c ". scripts/v1/core/ssm-safe-update.ps1; Update-AcademySSMParameter -Name '/academy/api/env' -KeyUpdates @{ CDN_HLS_SIGNING_SECRET = '$NEW_CDN_SK' } -ExpectMinKeys 50 -Wrapping plain"

# 3) Worker secret 갱신 (즉시 반영)
echo $NEW_CDN_SK | wrangler secret put CDN_HLS_SIGNING_SECRET

# 4) API 컨테이너 docker stop+rm+run --env-file 으로 reload (양쪽 API EC2)
```

### 7. 백엔드 CDN_HLS_BASE_URL 전환 (단계적)
**현재**: `pub-54ae...r2.dev` (public R2)
**목표**: `https://cdn.hakwonplus.com` (signed Worker)

```bash
# tenant 1 만 먼저: 백엔드에 tenant-scoped feature flag 또는 SSM 직접 단일 전환
pwsh -c ". scripts/v1/core/ssm-safe-update.ps1; Update-AcademySSMParameter -Name '/academy/api/env' -KeyUpdates @{ CDN_HLS_BASE_URL = 'https://cdn.hakwonplus.com' } -ExpectMinKeys 50 -Wrapping plain"
# API 컨테이너 reload (stop+rm+run)
```

### 8. R2 bucket public 차단 (마지막 단계 — 비가역 아님, 즉시 복구 가능)
**Cloudflare Dashboard**:
- R2 → academy-video → Settings → Public access → **Disable**
- (또는 Custom Domain `pub-54ae...r2.dev` 해제)

**검증**:
```bash
# 1) Worker 통한 signed URL — 정상 재생
# 2) public R2 직접 URL — 403/404
curl -I "https://pub-54ae4dcb984d4491b08f6c57023a1621.r2.dev/tenants/1/video/hls/284/master.m3u8"
```

## 롤백 절차

| 단계 | 롤백 방법 | 소요 |
|---|---|---|
| Step 8 (R2 public) | Dashboard → Public access → Enable | 30초 |
| Step 7 (CDN base URL) | SSM helper 로 `pub-54ae...r2.dev` 복원 + API reload | 2분 |
| Step 6 (SECRET 회전) | SSM helper 로 이전 값 + Worker secret 다시 등록 | 3분 |
| Step 5 (Route) | Dashboard → Triggers → Route Delete | 30초 |
| Step 3 (배포) | `wrangler delete` | 30초 |

전 단계 비가역적인 작업 **없음**.

## 검증 시나리오

### 정상 케이스
- 학생 로그인 → 영상 재생 → HLS master.m3u8 / variant index.m3u8 / segment .ts 모두 200
- 학원장 비공개 영상 → 다른 학원 학생이 접근 시 401 (백엔드가 signed URL 발급 안 함)

### 공격 케이스
- 직접 URL 입력 (no sig) → 401
- 다른 학원장 secret 으로 위조 → 403
- 만료된 exp → 401
- 다른 비디오 path 로 sig 재사용 (sig 변경 없이 path 만 교체) → 403

### Edge
- 동시 시청 100명 → CF Worker free tier 일 한도 100k req, 영상 한 편 60min = ~600 segments × 100 = 60k req. 안전 마진 충분.

## 비용
- CF Workers: 무료 (100k req/day)
- R2: 변동 없음 (R2↔CF egress 무료)
- 추가 비용 0

## 알려진 제약
- 백엔드 PROD `CDN_HLS_BASE_URL` 현재 `https://pub-54ae...r2.dev` 고정. Step 7 에서 일괄 전환 시 진행 중 시청자 다음 segment 요청부터 affected — m3u8 reload 시 자동 회복. 단기 ~10초 끊김 가능.
- 캐시 stampede: 첫 segment 요청 시 R2 fetch latency (~50ms). CF edge 캐시 hit 이후 5ms 수준.

## 관련 파일
- `infra/cdn_worker/src/index.js` — Worker code
- `infra/cdn_worker/wrangler.toml` — config
- `infra/cdn_worker/test/verify-parity.mjs` — 백엔드 서명 ↔ Worker parity test
- `apps/domains/video/cdn/cloudflare_signing.py` — 백엔드 서명 (변경 없음)
- `apps/domains/video/views/playback_mixin.py:206` — 서명 URL 생성 호출 (변경 없음)
