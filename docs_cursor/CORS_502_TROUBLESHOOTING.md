# CORS 에러 + 502 Bad Gateway 대응

## 현상

- 브라우저: `Access to XMLHttpRequest at 'https://api.hakwonplus.com/...' from origin 'https://hakwonplus.com' has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present on the requested resource.`
- 일부 요청에 `502 (Bad Gateway)` 동시 표시.

**502가 나오면 CORS 에러로 보이지만, 원인은 인프라(정책/서브넷/ALB)인 경우가 많음.** 아래 인프라 점검을 먼저 실행하자.

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

## 4. 502 해결 후

- API가 정상 응답하면 Django가 CORS 헤더를 붙이므로 CORS 에러 사라짐.
- 새 프론트 도메인 추가 시: `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` 모두 반영 (docs/REFERENCE.md).

## 참고

- **09-api-502-jobs-checklist.md** (archive): 502 시 ALB/SG 점검 요약.
- **ACADEMY_API_SQS_ACCESS_FIX.md**: API가 private subnet일 때 SQS 등 아웃바운드 접근 (NAT/VPC Endpoint).
