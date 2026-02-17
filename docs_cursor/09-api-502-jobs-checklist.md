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

## 3. 502 나면: ALB/보안그룹 점검 (한 번에)

**루트 키 env 넣은 뒤:**

```powershell
cd C:\academy
.\scripts\check_api_alb.ps1
```

- **[3] 8000 포트 인바운드 없음** 이면 → API SG에 8000 포트가 안 열려 있어서 ALB가 API에 못 붙는 상태.  
  ALB 보안그룹 ID 확인 (콘솔: EC2 → 로드밸런싱 → 로드밸런서 → 보안 그룹) 후:
  ```powershell
  aws ec2 authorize-security-group-ingress --group-id sg-0051cc8f79c04b058 --protocol tcp --port 8000 --source-group <ALB_SG_ID> --region ap-northeast-2
  ```
- 타깃 그룹에서 academy-api 인스턴스가 **healthy**인지 확인. unhealthy면 헬스체크 경로가 `/health` 인지, 포트 8000인지 확인.

## 4. API 서버에서 로그 확인

```bash
docker logs academy-api --tail 200 2>&1
docker ps | grep academy-api
```

---

요약: **1) check_api_alb.ps1** 로 원인 확인 → **2) 8000 인바운드 없으면** 위 `authorize-security-group-ingress` 실행 → **3) 코드 반영**은 API 서버에서 `git pull && bash scripts/deploy_api_on_server.sh` 또는 풀배포.
