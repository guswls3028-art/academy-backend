# API 502 / jobs 엔드포인트 점검 (최소 단계)

## 1. 코드 반영됨 (이미 적용)

- `JobStatusView`에 예외 처리 추가됨 → 에러 나도 500 JSON으로 응답하고, 로그에 상세 내용 남김.
- **배포해야 반영됨** (아래 2번).

## 2. API 서버에 배포 (택 1)

**방법 A – ECR 이미지 이미 있을 때 (코드만 반영 후 빌드·푸시 했다고 가정)**

```powershell
cd C:\academy
.\scripts\quick_api_restart.ps1
```

- academy-api EC2 찾아서 ECR에서 pull 후 컨테이너 재시작.

**방법 B – API 이미지 새로 빌드·푸시 후 재시작**

```powershell
cd C:\academy
.\scripts\quick_redeploy.ps1 -DeployTarget api
# 그 다음 API 인스턴스에서 컨테이너만 재시작하려면:
.\scripts\quick_api_restart.ps1
```

(quick_redeploy가 푸시까지 하면, quick_api_restart는 그 이미지 pull 후 재시작.)

## 3. 502 나면 서버 쪽 확인 (복붙용)

**API 인스턴스에서:**

```bash
# 컨테이너 로그 (최근 에러)
docker logs academy-api --tail 200 2>&1

# 컨테이너 살아 있는지
docker ps | grep academy-api
```

**로컬에서 API 응답 확인:**

```powershell
# 로그인 토큰 있으면 (실제 job_id로)
curl -s -o NUL -w "%{http_code}" -H "Authorization: Bearer YOUR_TOKEN" "https://api.hakwonplus.com/api/v1/jobs/실제job_id/"
# 200/404/500 나오면 API까지는 도달한 것. 502면 ALB/인스턴스 문제.
```

---

요약: **코드 수정은 완료.** → `quick_redeploy.ps1 -DeployTarget api` 로 이미지 빌드·푸시 후 `quick_api_restart.ps1` 한 번 돌리면 됨.
