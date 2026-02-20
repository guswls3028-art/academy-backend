# full_redeploy.ps1 SSH 흐름 분석 (STRICT INFRA MODE)

> **목적**: 실제 코드 기반으로 SSH 연결 흐름·불일치 항목 정리. 추측 금지.
> **작성일**: 2026-02-18

---

## 1. 흐름 다이어그램

```
full_redeploy.ps1 실행
        │
        ├── [1/3] 빌드 (SkipBuild 아니면)
        │     └── academy-build-arm64 → SSM Run Command (SSH 없음)
        │
        ├── [2/3] API 배포 (DeployTarget=all|api)
        │     │
        │     ├── Start-StoppedAcademyInstances (Name IN academy-*)
        │     │
        │     ├── Get-Ec2PublicIps
        │     │     └── aws ec2 describe-instances
        │     │           --filters tag:Name IN (academy-api, academy-ai-worker-cpu, ...)
        │     │           --query "...PublicIpAddress"
        │     │     └── 반환: @{ "academy-api" = "x.x.x.x", ... }
        │     │
        │     ├── .env SCP (API 서버)
        │     │     └── scp -i C:\key\backend-api-key.pem .env ec2-user@$apiIp:/home/ec2-user/.env
        │     │
        │     └── Deploy-One (academy-api)
        │           └── ssh -i C:\key\backend-api-key.pem ec2-user@$apiIp "docker pull ... && ..."
        │
        └── [3/3] Worker 배포
              │
              ├── WorkersViaASG=true  → ASG instance refresh (SSH 없음)
              │     └── start-instance-refresh (academy-video-worker-asg, academy-ai-worker-asg, academy-messaging-worker-asg)
              │
              └── WorkersViaASG=false → Deploy-One × 3 (각 worker SSH)
                    └── ssh -i C:\key\<worker-key>.pem ec2-user@$ip "docker pull ..."
```

---

## 2. SSH 연결에 사용되는 IP 출처

| 대상 | 출처 | 코드 위치 |
|------|------|-----------|
| API | `Get-Ec2PublicIps` → `$ips["academy-api"]` | full_redeploy.ps1:241 |
| Worker (WorkersViaASG=false) | 동일 `$ips` | full_redeploy.ps1:283-285 |

**Get-Ec2PublicIps 상세** (full_redeploy.ps1:63-76):

```powershell
aws ec2 describe-instances --region $Region `
  --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=academy-api,academy-ai-worker-cpu,academy-messaging-worker,academy-video-worker" `
  --query "Reservations[].Instances[].[Tags[?Key=='Name'].Value | [0], PublicIpAddress]" `
  --output text
```

- **필터**: running + tag Name IN (academy-api, academy-ai-worker-cpu, academy-messaging-worker, academy-video-worker)
- **반환**: `Name PublicIpAddress` 텍스트
- **중요**: PublicIpAddress만 사용. Private IP 미사용.

**PublicIpAddress가 None/없으면**: 해당 인스턴스는 결과에서 제외됨 (line 74: `$p[1] -ne "None"` 조건).

---

## 3. SSH 키 파일 출처

| 인스턴스 Name | 키 파일 | 전체 경로 |
|---------------|---------|-----------|
| academy-api | backend-api-key.pem | C:\key\backend-api-key.pem |
| academy-messaging-worker | message-key.pem | C:\key\message-key.pem |
| academy-ai-worker-cpu | ai-worker-key.pem | C:\key\ai-worker-key.pem |
| academy-video-worker | video-worker-key.pem | C:\key\video-worker-key.pem |

**코드**: full_redeploy.ps1:49-55, 98-99

**WorkersViaASG=true 시**: Worker 쪽 SSH는 수행하지 않음. API용 `backend-api-key.pem`만 사용.

---

## 4. Launch Template과 KeyName

**deploy_worker_asg.ps1** (lines 104, 134, 164):

- AI / Video / Messaging Launch Template JSON에 **KeyName 미포함**.
- `$KeyName = ""` 파라미터는 있으나 실제 LT 생성에 사용되지 않음.

**결과**:

- ASG 워커 인스턴스에는 KeyPair 미할당 → **SSH 불가**.
- `full_redeploy.ps1`은 WorkersViaASG 시 워커에 SSH하지 않으므로, KeyName 없음이 full_redeploy 실패 직접 원인은 아님.
- 필요 시 ASG 워커 디버깅용 SSH는 불가.

---

## 5. ASG 모드에서 SSH가 필요한 경우

**WorkersViaASG=true 일 때**:

- **API만** SSH 사용 (academy-api).
- 워커는 ASG instance refresh로 처리, SSH 없음.

따라서 SSH 실패는 **API 인스턴스(academy-api)** 기준으로만 발생 가능.

---

## 6. SSM 모드와 SSH 모드 혼합 여부

| 단계 | 방식 | 비고 |
|------|------|------|
| 빌드 | SSM Run Command | academy-build-arm64, SSH 없음 |
| API 배포 | SSH | academy-api, Public IP 필요 |
| Worker 배포 (WorkersViaASG) | ASG refresh | SSM user_data로 부팅, SSH 없음 |
| Worker 배포 (!WorkersViaASG) | SSH | 고정 EC2 3대 |

SSM과 SSH는 단계별로 분리되어 있으며, 한 스크립트 안에서 섞여 있지는 않음.

---

## 7. -NoCache 옵션이 SSH 경로에 미치는지

**영향 없음.**

- `-NoCache`: docker build 시 `--no-cache` 플래그 추가 (line 184-185).
- 빌드는 SSM Run Command로 수행되므로 SSH 경로와 무관.
- API/Worker 배포 경로와도 무관.

---

## 8. 불일치·위험 포인트

### 8.1 [핵심] academy-api에 Public IP 없음

**상황**: academy-api가 private subnet에만 있고 Public IP가 없는 경우.

**동작**:

- `Get-Ec2PublicIps`가 academy-api를 찾지 못하거나 PublicIpAddress=None으로 제외.
- `$ips["academy-api"]` = `$null`
- `Deploy-One`에서 `if (-not $Ip)` → "SKIP - No public IP" 후 `exit 1`.

**확인**:

```powershell
aws ec2 describe-instances --region ap-northeast-2 `
  --filters "Name=tag:Name,Values=academy-api" "Name=instance-state-name,Values=running" `
  --query "Reservations[].Instances[].[InstanceId,PublicIpAddress,PrivateIpAddress]" --output text
```

PublicIpAddress가 비어 있으면 위 시나리오에 해당.

**대응**:

1. academy-api를 public subnet으로 옮기거나  
2. Elastic IP 할당 또는  
3. Bastion/터널 경유로 API 배포 로직을 별도 구성

### 8.2 deploy_preflight.ps1 SSH 테스트 인자

**위치**: deploy_preflight.ps1:120

```powershell
$sshTest = ssh -o BatchMode=yes ... -i "`"$apiKeyPath`"`" ec2-user@$($ips["academy-api"]) "exit" 2>&1
```

`-i "`"$apiKeyPath`"`"` 는 `-i """C:\key\backend-api-key.pem"""` 형태로 해석될 수 있어, 경로에 공백이 있을 경우 문제 가능성 있음.

**권장**: `-i $apiKeyPath` 또는 `-i "$apiKeyPath"` 형태로 단순화.

### 8.3 API와 Worker SG 분리

- API: `academy-api-sg` (sg-0051cc8f79c04b058)
- Worker: `academy-worker-sg` (sg-02692600fbf8e26f7)
- full_redeploy 기본값: `SecurityGroupId = "sg-02692600fbf8e26f7"` (Worker SG)

이 SecurityGroupId는 **빌드 인스턴스**용이며, API 인스턴스 조회(Get-Ec2PublicIps)에는 tag:Name만 사용하므로 SG 불일치가 직접적인 SSH 실패 원인은 아님.

### 8.4 ASG 이름 매핑

full_redeploy.ps1 $asgMap (lines 265-269):

- academy-video-worker → academy-video-worker-asg ✅
- academy-ai-worker-cpu → academy-ai-worker-asg ✅
- academy-messaging-worker → academy-messaging-worker-asg ✅

deploy_worker_asg.ps1에서 생성하는 ASG 이름과 일치.

### 8.5 [확정] SG 22 포트 IP 제한 ("전부 안되거나 전부 되거나" 원인)

**확인된 상태** (2026-02-18 기준):

| SG | SSH 22 허용 대상 |
|----|------------------|
| academy-api-sg (sg-0051cc8f79c04b058) | 222.107.38.38/32 **만** |
| academy-worker-sg (sg-02692600fbf8e26f7) | 222.107.38.38/32, 0.0.0.0/0 |

**동작**:
- API는 222.107.38.38/32에서만 SSH 가능.
- 공인 IP가 바뀌면(집↔회사, VPN, 통신사 NAT 등) API SSH 전부 실패.
- 워커는 0.0.0.0/0 포함이라 상대적으로 덜함.

**"5개 전부 접속 안되거나 전부 되거나"** → API SG 22가 특정 IP만 허용하기 때문.

**대응**:
- 운영 안정: academy-api-sg에 `--cidr 0.0.0.0/0` 추가(키로 통제) 또는 현재 IP/32 주기적 갱신 스크립트.
- 보안 강화: API SSM 배포 전환으로 SSH 제거.

---

## 9. 체크리스트 (SSH 실패 시)

1. [ ] academy-api에 Public IP 있는지 확인 (위 aws ec2 describe-instances)
2. [ ] C:\key\backend-api-key.pem 존재 여부
3. [ ] academy-api tag Name=academy-api 인지
4. [ ] academy-api instance-state=running 인지
5. [ ] academy-api-sg 22번: 현재 공인 IP가 허용되는지 (`Invoke-RestMethod checkip.amazonaws.com` vs 222.107.38.38/32)
6. [ ] deploy_preflight.ps1 -TestSsh 실행 결과

---

## 10. 수정 권장 (승인 후 적용)

| 항목 | 현재 | 권장 | 비고 |
|------|------|------|------|
| SG 22 IP 제한 (API) | 222.107.38.38/32만 | 0.0.0.0/0(키로 통제) 또는 현재 IP 주기 갱신 | "전부 안됨" 주 원인 |
| Private IP 폴백 | 없음 | API가 Public IP 없을 때 Bastion/터널 또는 Private IP+터널 사용 옵션 | 설계 변경 필요 |
| deploy_preflight SSH -i | `"`"$apiKeyPath`"`"` | `$apiKeyPath` | 단순화(적용 완료) |
| Launch Template KeyName | 미설정 | 디버깅용 SSH 필요 시 KeyName 파라미터로 추가 | 선택 |

이 문서는 **코드 기준 분석**이며, 수정은 사용자 승인 후 진행할 것.
