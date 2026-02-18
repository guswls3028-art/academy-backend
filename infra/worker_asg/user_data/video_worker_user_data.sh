#!/bin/bash
# ASG Video Worker: 100GB 마운트 + Docker + ECR pull + run academy-video-worker
# EC2_IDLE_STOP_THRESHOLD=0 → self-stop 비활성화 (ASG가 scale-in으로 종료)
set -e
yum update -y
yum install -y docker
yum install -y ec2-instance-connect 2>/dev/null || true
systemctl start docker && systemctl enable docker

# 100GB 볼륨 마운트 (루트 nvme0n1 제외, nvme1n1 사용)
DEV=$(lsblk -d -n -o NAME | grep nvme | grep -v nvme0n1 | tail -1)
if [ -n "$DEV" ] && [ -b "/dev/${DEV}" ]; then
  mkfs -t ext4 "/dev/${DEV}" 2>/dev/null || true
  mkdir -p /mnt/transcode
  mount "/dev/${DEV}" /mnt/transcode || true
  grep -q "/mnt/transcode" /etc/fstab 2>/dev/null || echo "/dev/${DEV} /mnt/transcode ext4 defaults,nofail 0 2" >> /etc/fstab
  # 컨테이너 appuser(UID 1000)가 /tmp 쓰기 가능하도록
  chown -R 1000:1000 /mnt/transcode
fi

ENV_FILE="/opt/academy/.env"
mkdir -p /opt/academy
aws ssm get-parameter --name /academy/workers/env --with-decryption --query Parameter.Value --output text --region ap-northeast-2 > "$ENV_FILE" 2>/dev/null || true

ECR="{{ECR_REGISTRY}}"
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin "$ECR"
docker pull "$ECR/academy-video-worker:latest"
docker stop academy-video-worker 2>/dev/null || true
docker rm academy-video-worker 2>/dev/null || true

# docker run 재시도 (실패 시 10초 후 최대 3회)
for i in 1 2 3; do
  docker rm -f academy-video-worker 2>/dev/null || true
  if docker run -d --name academy-video-worker --restart unless-stopped --memory 4g \
    --env-file "$ENV_FILE" \
    -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker \
    -e EC2_IDLE_STOP_THRESHOLD=0 \
    -v /mnt/transcode:/tmp \
    "$ECR/academy-video-worker:latest"; then
    break
  fi
  echo "docker run attempt $i failed, retrying in 10s..."
  sleep 10
done

# 결과 확인 (cloud-init-output.log에 남음, 디버깅용)
docker ps -a
echo "Video worker user data done"
