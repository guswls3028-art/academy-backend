# EC2 인스턴스 생성 가이드 (워커 3대)

**기준**: `AWS_500_START_DEPLOY_GUIDE.md` §4, §5, §7, §8, §9.  
**대상**: API EC2는 이미 있다고 가정. **Messaging / Video / AI Worker** 3대 생성부터.

---

## 0. 전제 (이미 있으면 스킵)

- [ ] **리전**: ap-northeast-2 (서울) 선택
- [ ] **IAM 역할** (이름 예: `academy-ec2-role`): SQS(academy-*), ECR pull, EC2 Self-stop. 인스턴스 프로필로 연결.
- [ ] **보안 그룹**
  - `academy-worker-sg`: 인바운드 **SSH 22** from 본인 IP
  - RDS 보안 그룹(`rds-academy-sg`) 인바운드에 **5432** from `academy-worker-sg` 추가됨

---

## 1. 콘솔에서 공통 설정

1. **EC2** → **인스턴스** → **인스턴스 시작**
2. **이름**: 아래 표의 "이름" 참고 (구분용)
3. **AMI**: **Amazon Linux 2023**
4. **인스턴스 유형**: 아래 표대로
5. **키 페어**: 기존 키 선택 또는 새로 생성 (SSH용)
6. **네트워크**: API·RDS와 **같은 VPC** (기본 VPC 가능)
7. **IAM 인스턴스 프로필**: `academy-ec2-role` (또는 사용 중인 EC2용 역할)
8. **보안 그룹**: **academy-worker-sg** (워커 3대 공통)

---

## 2. 인스턴스별 스펙 (표만 보고 콘솔에서 입력)

| 이름(태그) | 인스턴스 유형 | 루트 볼륨 | 추가 볼륨 | 비고 |
|------------|----------------|-----------|-----------|------|
| academy-messaging-worker | **t4g.micro** | 8 GB (또는 20 GB) | 없음 | 상시 1대 |
| academy-video-worker | **t4g.medium** | 8 GB | **+ 100 GB gp3** (아래 §3에서 마운트) | 4GB RAM, 트랜스코딩용 |
| academy-ai-worker-cpu | **t4g.micro** 또는 **t4g.small** | 8 GB (또는 20 GB) | 없음 | Self-stop 사용 시 t4g.micro 가능 |

- **Video만** 스토리지 단계에서 **스토리지 추가** 클릭 → **100 GB** gp3 추가. (장치 이름은 기본값 `/dev/sdb` 등으로 두면 됨.)

---

## 3. 인스턴스 생성 후 — Video만: 100GB 볼륨 마운트

Video Worker EC2에 SSH 접속한 뒤 아래만 실행. (추가한 100GB가 `/dev/nvme1n1` 같은 이름일 수 있음. `lsblk`로 확인.)

```bash
# 볼륨 장치 확인 (100G 블록 디바이스 확인)
lsblk
```

- 100G 디스크가 **미할당**이면 (예: `nvme1n1`), 아래 순서로 파티션·포맷·마운트.

```bash
# 파티션 생성 (n → p → 1 → 엔터 → 엔터 → w). 장치 경로는 lsblk 결과에 맞게 변경.
sudo gdisk /dev/nvme1n1
# n 엔터, p 엔터, 1 엔터, 엔터, 엔터, w 엔터, Y 엔터
```

- gdisk 대신 간단히 한 번에:

```bash
# 100G 디스크가 /dev/nvme1n1 일 때 (Amazon Linux 2023)
sudo growpart /dev/nvme1n1 1 2>/dev/null || true
sudo mkfs -t ext4 /dev/nvme1n1
sudo mkdir -p /mnt/transcode
sudo mount /dev/nvme1n1 /mnt/transcode
echo '/dev/nvme1n1 /mnt/transcode ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
```

- **파티션이 이미 있으면** (예: `nvme1n1p1`):

```bash
sudo mkfs -t ext4 /dev/nvme1n1p1
sudo mkdir -p /mnt/transcode
sudo mount /dev/nvme1n1p1 /mnt/transcode
echo '/dev/nvme1n1p1 /mnt/transcode ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
```

- 확인:

```bash
df -h
```

→ **/mnt/transcode** 가 약 **100G**로 보이면 OK.

---

## 4. (선택) Video EC2에 Swap 2GB

가이드 권장. SSH 접속 후:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 5. 요약 체크

- [ ] Messaging EC2: t4g.micro, academy-worker-sg, IAM 프로필
- [ ] Video EC2: t4g.medium, 100GB 추가 볼륨, academy-worker-sg, IAM 프로필
- [ ] Video EC2: SSH 후 100GB를 `/mnt/transcode`에 마운트, `df -h` 확인
- [ ] AI Worker EC2: t4g.micro 또는 t4g.small, academy-worker-sg, IAM 프로필

이후 각 EC2에 `.env` 복사하고 `DEPLOY_STEP_BY_STEP_CHECKLIST.md` Step 7·8·9대로 Docker pull·run 하면 됨.
