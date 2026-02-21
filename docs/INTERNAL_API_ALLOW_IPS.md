# INTERNAL_API_ALLOW_IPS — Lambda → API 스케일 필수

Lambda가 **새 VPC(10.1.0.0/16)** 에 있고, **VPC Peering**으로 API(172.30.3.142)를 부르는 경우, Django `IsLambdaInternal` 권한에서 **요청 소스 IP**를 검사합니다.  
`INTERNAL_API_ALLOW_IPS`에 **10.1.0.0/16**이 없으면 Lambda 요청이 403이 되어 BacklogCount를 못 가져오고, **TargetTracking이 스케일하지 않습니다.**

## API 서버 EC2에서 할 일

1. **.env에 추가 (기존 172.30만 있으면 10.1 추가)**

   ```bash
   nano /home/ec2-user/.env
   ```

   다음 한 줄 추가 또는 수정:

   ```env
   INTERNAL_API_ALLOW_IPS=172.30.0.0/16,10.1.0.0/16
   ```

2. **컨테이너 재기동 (필수)**  
   settings는 컨테이너 기동 시점에만 로드되므로, 반드시:

   ```bash
   cd /home/ec2-user/academy
   bash scripts/refresh_api_container_env.sh
   ```

## 코드 동작

- `apps/api/config/settings/base.py`: `INTERNAL_API_ALLOW_IPS`를 `os.environ.get("INTERNAL_API_ALLOW_IPS", "").strip()` 으로 읽음.
- `apps/core/permissions.py` `IsLambdaInternal`: `X-Internal-Key` 일치 후, `INTERNAL_API_ALLOW_IPS`가 있으면 클라이언트 IP(`X-Forwarded-For` 또는 `REMOTE_ADDR`)가 지정된 CIDR 중 하나에 포함되는지 검사. 비어 있으면 IP 검사 생략.
