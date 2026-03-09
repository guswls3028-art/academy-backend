# CORS 에러 + 502 Bad Gateway 대응

## 현상

- 브라우저: `Access to XMLHttpRequest at 'https://api.hakwonplus.com/...' from origin 'https://tchul.com' has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present on the requested resource.`
- 일부 요청에 `502 (Bad Gateway)` 동시 표시.

**502가 나오면 CORS 에러로 보이지만, 원인은 인프라(정책/서브넷/ALB)이거나 upstream 비정상인 경우가 많음.**

## 코드베이스에서 한 일

1. **CORS 설정**: `prod.py`·`base.py`에 `https://tchul.com`, `https://www.tchul.com` 이미 포함됨. 추가 수정 없음.
2. **Django**: `CorsResponseFixMiddleware`가 5xx 등 모든 Django 응답에 CORS 보강. 502가 **Django까지 도달한 뒤** 나가는 경우는 없음(502는 보통 프록시/ALB에서 반환).
3. **nginx**: `docker/nginx/default.conf`, `infra/nginx/academy-api.conf`에 **502/503/504 시 CORS 헤더 추가** 적용. nginx가 502를 반환할 때(upstream 연결 실패·타임아웃 등) `Access-Control-Allow-Origin: $http_origin` 등을 붙여 브라우저가 "CORS policy" 대신 502 응답을 받도록 함.  
   → **ALB가 타깃에 연결조차 못 해서 ALB가 502를 반환하는 경우**는 nginx를 거치지 않으므로, 아래 인프라 점검으로 해결해야 함.

## 실제 점검 결과 (2026-03-09)

루트 키로 AWS 인프라 접근 후 확인한 내용.

| 항목 | 결과 |
|------|------|
| ALB | academy-v1-api-alb active, DNS 정상 |
| ALB SG | sg-0405c1afe368b4e6b |
| 타깃 그룹 | academy-v1-api-tg, HealthCheckPath=/healthz, Port 8000 |
| 타깃 헬스 | **healthy** (i-06419d8dbee091e3c) |
| API 인스턴스 SG | sg-03cf8c8f38f477687 (academy-v1-sg-app), 8000 허용 172.30.0.0/16 |
| GET https://api.hakwonplus.com/healthz | **200** |
| GET /api/v1/core/me/ (Origin: https://tchul.com) | **401** + access-control-allow-origin: https://tchul.com |
| OPTIONS /api/v1/core/me/ (preflight) | **200** + CORS 헤더 정상 |

**결론:** 현재 구간에서는 타깃 정상·CORS 정상. 502 재발 시 nginx 502 CORS 설정 배포 후에는 502 응답에도 CORS가 붙음.

---

## 1. 502 = 인프라 점검 (정책·서브넷·ALB) — **먼저 할 것**

같은 502/CORS 현상이 반복되면 **보안 그룹(8000 포트)·타겟 그룹(healthy)·서브넷/라우팅**을 의심.

### 1) ALB / 타겟 그룹 / API 보안그룹 한 번에 확인

```powershell
# AWS 자격증명 설정 후
.\scripts\check_api_alb.ps1
```

- **[3] 8000 포트 인바운드 없음**  
  → API EC2 보안 그룹에 **8000 포트**가 ALB 보안 그룹에서 오는 트래픽을 허용하지 않음.  
  → ALB가 API에 연결 못 해서 502.  
  → 스크립트가 안내하는 `authorize-security-group-ingress` 실행 (API SG에 port 8000 from ALB SG).

- **타겟 그룹 unhealthy**  
  → ALB가 타겟(academy-api)에 `/health` 요청 실패.  
  → 헬스체크 경로 `/health`, 포트 **8000** 인지 확인.  
  → API가 **private subnet**이면 ALB ↔ API 서브넷/라우팅이 서로 도달 가능한지 확인 (같은 VPC, 라우팅 테이블).

- **academy-api 인스턴스 없음/다운**  
  → EC2 상태·Docker 컨테이너 확인.

### 2) 서브넷/라우팅 (private subnet일 때)

- academy-api가 **private subnet** (`subnet-049e711f41fdff71b` 등)에 있으면:  
  ALB → API로 가는 경로가 있어야 함 (같은 VPC, ALB가 해당 서브넷으로 라우팅 가능).  
- NAT/인터넷 문제와 502는 별개: 502는 **ALB → API(8000)** 구간이어서, **ALB와 API가 서로 통신 가능한 서브넷/보안그룹**이면 됨.

### 3) 정리

| 확인 항목 | 내용 |
|-----------|------|
| API 보안그룹 | 8000 인바운드 from ALB SG |
| 타겟 그룹 | academy-api **healthy**, 헬스체크 `/health` 포트 8000 |
| 서브넷/라우팅 | ALB → API EC2 도달 가능 (같은 VPC, SG 허용) |

---

## 2. 원인 요약 (CORS는 결과)

1. **502는 Django가 아니라 프록시(ALB 등)에서 반환**됨.  
   - 백엔드 다운, 타임아웃, **ALB가 타겟에 연결 실패** 시 502 → 응답에 CORS 헤더 없음.
2. **Preflight(OPTIONS)도 502**면 "preflight doesn't pass access control check"로 보임.
3. **Django CORS 설정은 이미 올바름** (`https://hakwonplus.com` 포함). 502만 해소하면 CORS 에러 사라짐.

## 3. 그 다음 점검 (API 프로세스)

1. **API 서버 상태**  
   - EC2: `sudo docker ps`, `sudo docker logs academy-api`.  
   - Gunicorn 다운/OOM/재시작 루프 여부.

2. **CORS 설정 변경 불필요**  
   - 502 구간 해소하면 CORS 에러는 사라짐.

## tchul.com / storage 등 특정 경로에서만 CORS가 뜨는 경우

- **같은 API(api.hakwonplus.com)라도** 특정 엔드포인트(예: `/api/v1/messages/templates/`, `/api/v1/storage/`)만 502/500을 반환하면, 그 요청만 "CORS policy"로 보임.
- **조치**: F12 → Network에서 실패한 요청을 클릭해 **Status가 502/504/500인지** 확인. 502면 위 "1. 502 = 인프라 점검"대로 ALB·타깃·SG 확인. 500이면 API 로그에서 해당 경로 스택 트레이스 확인.
- **배포**: `infra/nginx/academy-api.conf`의 502 시 CORS 보강이 **실제 API 서버(EC2)에 반영**돼 있어야 함. 수정 후 `sudo nginx -t && sudo systemctl reload nginx` (또는 해당 서버의 배포 절차) 실행.

## 4. 502 해결 후

- API가 정상 응답하면 Django가 CORS 헤더를 붙이므로 CORS 에러 사라짐.
- 새 프론트 도메인 추가 시: `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` 모두 반영 (docs/REFERENCE.md).

## 참고

- **09-api-502-jobs-checklist.md** (archive): 502 시 ALB/SG 점검 요약.
- **ACADEMY_API_SQS_ACCESS_FIX.md**: API가 private subnet일 때 SQS 등 아웃바운드 접근 (NAT/VPC Endpoint).
