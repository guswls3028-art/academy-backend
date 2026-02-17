# 배포 셋팅 — 현재 코드 기준 (추측 없음)

아래는 **스크립트·인프라 코드에 적힌 값만** 정리한 것. 수정 이력이나 “원래 의도”는 포함하지 않음.

---

## 1. 공통 값 (스크립트 기본값)

| 항목 | 값 | 쓰는 스크립트 |
|------|-----|----------------|
| Region | ap-northeast-2 | full_redeploy, deploy_worker_asg, redeploy_worker_asg |
| Subnet (빌드/API용 1개) | subnet-07a8427d3306ce910 | full_redeploy.ps1 |
| Subnet (워커 ASG용 2개) | subnet-07a8427d3306ce910, subnet-09231ed7ecf59cfa4 | redeploy_worker_asg → deploy_worker_asg |
| Security Group | sg-02692600fbf8e26f7 | full_redeploy, redeploy_worker_asg |
| IAM 역할 | academy-ec2-role | full_redeploy (빌드 인스턴스), deploy_worker_asg (워커 LT) |
| Git URL (빌드용) | 인자로 전달. 예: https://github.com/guswls3028-art/academy-backend.git | full_redeploy -GitRepoUrl |
| SSH 키 디렉터리 | C:\key | full_redeploy.ps1 $KeyDir |

---

## 2. full_redeploy.ps1 동작 (코드 기준)

- **빌드:**  
  - 기존 인스턴스 태그 Name=academy-build-arm64 있으면 재사용(실행 중/중지 상태 확인 후 필요 시 start).  
  - 없으면 run-instances 1대 생성: SubnetId 위 1개, SecurityGroupId 위 1개, IAM profile academy-ec2-role, user_data로 docker+git 설치.  
  - 퍼블릭 IP: run-instances에 NetworkInterfaces/AssociatePublicIp 지정 없음 → 서브넷 기본(자동 할당) 따름.
- **빌드 후:** SSM send-command로 위 인스턴스에서 git clone/pull → docker build → ECR push. (api, messaging-worker, video-worker, ai-worker-cpu)
- **API 배포:**  
  - 태그 Name=academy-api 인 running 인스턴스의 PublicIp에 SSH.  
  - 사용 키: C:\key\backend-api-key.pem.  
  - 원격 명령: ECR 로그인 → pull → 기존 academy-api 컨테이너 중지/삭제 → docker run (env-file .env, 8000:8000).
- **워커 (-WorkersViaASG 시):**  
  - 고정 EC2 SSH 안 함.  
  - ASG 이름 academy-messaging-worker-asg, academy-ai-worker-asg, academy-video-worker-asg 에 대해 start-instance-refresh 만 호출.

---

## 3. redeploy_worker_asg.ps1 동작 (코드 기준)

- **1단계:** deploy_worker_asg.ps1 호출.  
  - 인자: SubnetIds = "subnet-07a8427d3306ce910,subnet-09231ed7ecf59cfa4", SecurityGroupId = "sg-02692600fbf8e26f7", IamInstanceProfileName = "academy-ec2-role", UploadEnvToSsm:$false, AttachEc2Policy:$false, GrantSsmPutToCaller:$false.
- **2단계:** setup_worker_iam_and_ssm.ps1 호출 (SkipSetup 아니면). SsmUserName "admin97", 같은 IAM 인스턴스 프로필, 같은 Region.

---

## 4. deploy_worker_asg.ps1 — Launch Template (코드에 적힌 대로)

- **AMI:** 지정 없으면 최신 Amazon Linux 2023 (arm64, ecs 제외). 없으면 amzn2 최신 arm64.
- **공통:** IamInstanceProfile Name = 파라미터로 받은 값(기본 academy-ec2-role), UserData = infra/worker_asg/user_data/ 해당 sh ({{ECR_REGISTRY}} 치환).

| 워커 | Launch Template 이름 | InstanceType | 네트워크 (코드 상) | 볼륨 |
|------|----------------------|-------------|---------------------|------|
| AI | academy-ai-worker-asg | t4g.small | SecurityGroupIds: [sg-02692600fbf8e26f7] | 기본 |
| Video | academy-video-worker-asg | t4g.medium | NetworkInterfaces: DeviceIndex 0, Groups [sg-02692600fbf8e26f7], AssociatePublicIpAddress: true | /dev/xvda 30GB gp3, /dev/sdb 100GB gp3 |
| Messaging | academy-messaging-worker-asg | t4g.small | NetworkInterfaces: DeviceIndex 0, Groups [sg-02692600fbf8e26f7], AssociatePublicIpAddress: true | 기본 |

→ **지금 코드는 AI만 SecurityGroupIds, Video/Messaging은 NetworkInterfaces+AssociatePublicIpAddress.**

---

## 5. 워커 ASG (deploy_worker_asg.ps1 코드)

- **AI:** academy-ai-worker-asg, Min=1, Max=파라미터(기본 20), vpc-zone-identifier = 위 SubnetIds 2개. Target tracking 스케일링(큐 깊이 등).
- **Video:** academy-video-worker-asg, Min=0, Max=20, 같은 서브넷 2개.
- **Messaging:** academy-messaging-worker-asg, Min=1, Max=20, 같은 서브넷 2개.

---

## 6. 워커 부팅 시 (user_data 스크립트)

- **env:** SSM Parameter /academy/workers/env (with-decryption) → /opt/academy/.env 로 저장 후 docker run --env-file 로 사용.
- **이미지:** ECR에서 academy-messaging-worker:latest, academy-video-worker:latest, academy-ai-worker-cpu:latest pull 후 동일 이름 컨테이너 실행.
- **ECR 주소:** Launch Template UserData에 {{ECR_REGISTRY}} 로 들어가며, deploy_worker_asg.ps1에서 현재 계정으로 "${AccountId}.dkr.ecr.${Region}.amazonaws.com" 치환.

---

## 7. SSH 키 (full_redeploy.ps1 상)

| 인스턴스 태그 Name | 키 파일 (C:\key 아래) |
|--------------------|------------------------|
| academy-api | backend-api-key.pem |
| academy-messaging-worker | message-key.pem |
| academy-ai-worker-cpu | ai-worker-key.pem |
| academy-video-worker | video-worker-key.pem |

→ **WorkersViaASG 사용 시** full_redeploy는 이 키로 SSH하지 않고, ASG instance refresh만 함. 따라서 위 키는 **고정 EC2**가 있을 때만 사용됨. 현재 플로우는 ASG 워커만 띄우는 구조.

---

이 문서는 **지금 repo에 있는 코드가 이렇게 동작한다**는 사실만 적은 것. “이게 맞다/틀리다”나 추가 권장 사항은 넣지 않음.
