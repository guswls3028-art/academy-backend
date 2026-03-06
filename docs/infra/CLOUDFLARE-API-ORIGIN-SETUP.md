# Cloudflare api.hakwonplus.com → ALB Origin 설정

**목적:** HTTPS 프론트(Cloudflare Pages)에서 API 호출 시 Mixed Content 방지.  
api.hakwonplus.com을 ALB로 프록시하면 HTTPS로 API 접근 가능.

## 현재 상태
- **ALB**: `academy-v1-api-alb-1244943981.ap-northeast-2.elb.amazonaws.com` (HTTP 80)
- **api.hakwonplus.com**: Cloudflare DNS (104.21.x.x, 172.67.x.x)
- **필요**: api.hakwonplus.com → ALB origin 설정

## Cloudflare 설정 절차

1. **Cloudflare Dashboard** → **DNS** → **Records**
2. `api.hakwonplus.com` 레코드 확인:
   - **Type**: CNAME 또는 A
   - **Proxy status**: Proxied (주황색 구름)
   - **Target**: `academy-v1-api-alb-1244943981.ap-northeast-2.elb.amazonaws.com` (CNAME이면)

3. **CNAME이 없는 경우**:
   - Add record
   - Type: CNAME
   - Name: api (또는 api.hakwonplus.com)
   - Target: `academy-v1-api-alb-1244943981.ap-northeast-2.elb.amazonaws.com`
   - Proxy: Proxied (ON)

4. **SSL/TLS**:
   - **Settings** → **SSL/TLS** → **Full** 또는 **Full (strict)**
   - Origin이 ALB(HTTP)이므로 Cloudflare가 HTTPS 종료

5. **설정 후 검증**:
   ```powershell
   curl -s "https://api.hakwonplus.com/healthz"
   # 예상: {"status":"ok","service":"academy-api"}
   ```

## 프론트엔드 설정

Cloudflare 설정 후:
- **Cloudflare Pages** Variables: `VITE_API_BASE_URL=https://api.hakwonplus.com`
- 또는 `.env.production`: `VITE_API_BASE_URL=https://api.hakwonplus.com` 후 재빌드

## ALB 직접 사용 (로컬/개발)

- **로컬 preview** (http://localhost:4173): `VITE_API_BASE_URL=http://academy-v1-api-alb-1244943981.ap-northeast-2.elb.amazonaws.com` 사용 가능
- **HTTPS 페이지**: Mixed Content로 인해 ALB(HTTP) 직접 호출 불가 → api.hakwonplus.com(HTTPS) 필요
