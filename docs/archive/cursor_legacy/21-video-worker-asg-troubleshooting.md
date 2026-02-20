# 비디오 워커 ASG · 인스턴스 확인 가이드

## 0. ECS AMI로 뜬 경우 (원인 및 수정)

- **증상**: 인스턴스 이름은 `academy-video-worker`인데 `docker ps`에 `academy-video-worker` 없고 **ecs-agent**만 있음.  
  인스턴스 세부정보에서 **AMI 이름**이 `al2023-ami-ecs-hvm-*` (ECS 최적화)로 나옴.
- **원인**: Launch Template이 **ECS 최적화 AMI**를 쓰고 있음.  
  `deploy_worker_asg.ps1`의 AMI 필터(`al2023-ami-*-kernel-6.1-arm64`)가 ECS AMI도 포함해, 최신 AMI가 ECS로 잡혔을 수 있음.
- **코드 수정**: `scripts/deploy_worker_asg.ps1`에서 ECS AMI를 제외하도록 수정됨(이름에 `ecs` 포함 AMI 미사용).
- **조치**  
  1. **Launch Template을 올바른 AMI로 다시 적용**  
     - 동일 옵션으로 `deploy_worker_asg.ps1` 다시 실행(서브넷·보안그룹·IAM 프로필 등 그대로).  
     - 그러면 **일반 AL2023** AMI로 새 Launch Template 버전이 생성되고 기본 버전으로 설정됨.  
  2. **인스턴스 새로 띄우기**  
     - `full_redeploy.ps1 -WorkersViaASG` 또는 ASG에서 **인스턴스 새로 고침** 실행.  
     - 새 인스턴스는 일반 AL2023 + user_data로 `academy-video-worker` 컨테이너만 뜸.
- **당장 한 대만이라도 쓰고 싶을 때**  
  - 현재 ECS AMI 인스턴스에 SSH 접속 후, 아래 4번처럼 **수동으로** `academy-video-worker` 컨테이너만 실행해 두면 됨.  
  - 이후 ASG/Launch Template 수정 후 인스턴스 새로 고침하면 정상 구성으로 맞출 수 있음.

---

## 1. 지금 접속한 인스턴스가 비디오 워커가 아닐 수 있음

- **비디오 워커 ASG** (`academy-video-worker-asg`)는 **Amazon Linux 2023 일반 AMI**로 뜨는 것이 맞습니다.  
  배너: `Amazon Linux 2023` (ECS Optimized 아님)
- 접속한 서버 배너가 **`Amazon Linux 2023 (ECS Optimized)`** 이면 **ECS용 AMI**로 뜬 것이고,  
  그 위에는 ecs-agent만 있고 `academy-video-worker`는 user_data 실패 또는 미실행으로 없을 수 있습니다.  
  → 위 0번(ECS AMI 원인) 참고 후, Launch Template 수정 + 인스턴스 새로 고침 또는 당장은 4번 수동 기동.

---

## 2. 비디오 워커 인스턴스 찾기

### AWS 콘솔

1. **EC2 → 인스턴스**
2. 필터: **이름** `academy-video-worker`  
   또는 **Auto Scaling 그룹** `academy-video-worker-asg`
3. 나온 인스턴스의 **퍼블릭 IP**로 SSH 접속

### CLI

```powershell
# 비디오 워커 ASG에 속한 인스턴스 ID
aws autoscaling describe-auto-scaling-groups --region ap-northeast-2 `
  --auto-scaling-group-names academy-video-worker-asg `
  --query "AutoScalingGroups[0].Instances[].InstanceId" --output text

# 인스턴스 이름 + 퍼블릭 IP
aws ec2 describe-instances --region ap-northeast-2 `
  --filters "Name=tag:Name,Values=academy-video-worker" "Name=instance-state-name,Values=running" `
  --query "Reservations[].Instances[].[Tags[?Key=='Name'].Value|[0],PublicIpAddress]" --output text
```

---

## 3. -WorkersViaASG 로 풀배포했을 때 동작

- `full_redeploy.ps1 -WorkersViaASG` 는 **고정 EC2에 SSH해서 docker 올리지 않습니다.**
- **ASG instance refresh** 만 수행:  
  `academy-video-worker-asg` 등에 대해 기존 인스턴스 교체(종료 후 새 인스턴스 기동)만 합니다.
- 새 인스턴스는 **Launch Template user_data** 로:
  - Docker 설치
  - **100GB 추가 EBS**(LT의 `/dev/sdb`)를 nvme1n1 등으로 찾아 `/mnt/transcode`에 마운트
  - SSM에서 `.env` 받기
  - ECR pull → `docker run academy-video-worker`

**Video 워커만** 100GB 추가 볼륨을 쓰므로, LT에 BlockDeviceMapping이 있어야 하고 user_data 마운트가 성공해야 함. 마운트 실패 시 `-v /mnt/transcode:/tmp` 가 빈 디렉터리를 써서 트랜스코딩 중 디스크 부족 등 발생 가능. 문제 시 `cloud-init-output.log` 확인 후, 필요하면 `deploy_worker_asg.ps1` 실행해 LT 재적용 후 instance refresh.

그래서 **비디오 워커가 돌아가는 곳은 “이름이 academy-video-worker 인 ASG에서 띄운 인스턴스”** 한 대(또는 scale-out 시 여러 대)입니다.  
**ECS Optimized 인스턴스(ecs-agent만 있는 서버)에는 비디오 워커가 없습니다.**

---

## 4. 비디오 워커 인스턴스에 접속했는데 컨테이너가 없을 때

같은 인스턴스에서 `docker ps | grep video` 해도 안 나오면 user_data 실패 가능성이 있습니다.

```bash
# user_data(cloud-init) 로그
sudo cat /var/log/cloud-init-output.log

# 마지막 부분에 "Video worker user data done" / "docker ps -a" 출력이 있어야 함
# 에러가 있으면: SSM /academy/workers/env 없음, ECR 권한, 디스크 마운트 실패 등 확인
```

필요 시 수동 기동:

```bash
# .env는 SSM에서 받았다고 가정
sudo aws ssm get-parameter --name /academy/workers/env --with-decryption --query Parameter.Value --output text --region ap-northeast-2 > /opt/academy/.env
ECR="809466760795.dkr.ecr.ap-northeast-2.amazonaws.com"
sudo aws ecr get-login-password --region ap-northeast-2 | sudo docker login --username AWS --password-stdin $ECR
sudo docker pull $ECR/academy-video-worker:latest
sudo docker run -d --name academy-video-worker --restart unless-stopped --memory 4g \
  --env-file /opt/academy/.env \
  -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker \
  -e EC2_IDLE_STOP_THRESHOLD=0 \
  -v /mnt/transcode:/tmp \
  $ECR/academy-video-worker:latest
docker ps | grep video
```

---

## 5. 요약

| 상황 | 조치 |
|------|------|
| 접속한 서버가 **ECS Optimized** 배너 | 다른 서버에 접속한 것. 위 2번으로 **Name=academy-video-worker** 인스턴스 찾아서 접속 |
| **academy-video-worker** 인스턴스인데 컨테이너 없음 | `cloud-init-output.log` 확인 후, 필요 시 위 4번 수동 기동 |
| **100GB 볼륨** 마운트 실패·디스크 부족 | `cloud-init-output.log`에서 `DEV=`/`mount` 확인. LT에 BlockDeviceMapping 반영돼 있는지 확인 후 `deploy_worker_asg.ps1` 재실행 + instance refresh |
| ASG Min=0 이라 인스턴스가 0대 | 영상 작업 넣으면 scale-out 되도록 되어 있음. 상시 1대 원하면 ASG Min=1 로 변경 |
