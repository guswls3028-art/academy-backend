# 비디오 워커 ASG · 인스턴스 확인 가이드

## 1. 지금 접속한 인스턴스가 비디오 워커가 아닐 수 있음

- **비디오 워커 ASG** (`academy-video-worker-asg`)는 **Amazon Linux 2023 일반 AMI**로 뜹니다.  
  배너: `Amazon Linux 2023` (ECS Optimized 아님)
- 접속한 서버 배너가 **`Amazon Linux 2023 (ECS Optimized)`** 이면 **ECS용 인스턴스**이고,  
  우리가 띄우는 비디오 워커 호스트가 아닙니다.  
  → 그 위에는 `ecs-agent`만 있고 `academy-video-worker` 컨테이너는 없습니다.

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
  - `/mnt/transcode` 마운트 (100GB)
  - SSM에서 `.env` 받기
  - ECR pull → `docker run academy-video-worker`

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
| ASG Min=0 이라 인스턴스가 0대 | 영상 작업 넣으면 scale-out 되도록 되어 있음. 상시 1대 원하면 ASG Min=1 로 변경 |
