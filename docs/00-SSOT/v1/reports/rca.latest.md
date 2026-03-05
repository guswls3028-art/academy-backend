# V1 RCA (Root Cause Analysis) — API /health unreachable, ALB target unhealthy

**생성 시각:** 2026-03-06 (KST)  
**SSOT:** docs/00-SSOT/v1/params.yaml  
**리전:** ap-northeast-2

---

## 1) ASG 소속 vs 레거시 인스턴스 구분

### ASG describe-auto-scaling-groups (academy-v1-api-asg)
- **MinSize:** 2, **MaxSize:** 4, **DesiredCapacity:** 2
- **Instances (ASG 소속):**
  - i-013a69fa815cf30cb (InService, Healthy, LT version 6) — 172.30.2.107
  - i-0b666c22116dc6520 (InService, Healthy, LT version 6) — 172.30.0.133

### EC2 describe-instances (Name=academy-v1-api, running/pending)
| InstanceId             | State   | PrivateIp   |
|------------------------|--------|-------------|
| i-0b666c22116dc6520    | running| 172.30.0.133 |
| i-013a69fa815cf30cb    | running| 172.30.2.107 |

### 결론
- **ASG 소속 인스턴스 IDs:** i-013a69fa815cf30cb, i-0b666c22116dc6520
- **ASG 외부(레거시 후보) 인스턴스 IDs:** 없음 (Name=academy-v1-api 인스턴스 2대 모두 ASG 소속)

---

## 2) TargetGroup unhealthy — Reason/Description

### describe-target-groups (academy-v1-api-tg)
- **Port:** 8000, **Protocol:** HTTP
- **HealthCheckPath:** /health, **Matcher:** 200
- **HealthCheckTimeoutSeconds:** 5

### describe-target-health
| Target (InstanceId)    | Port | State     | Reason         | Description        |
|-------------------------|------|-----------|----------------|--------------------|
| i-013a69fa815cf30cb     | 8000 | unhealthy | Target.Timeout | Request timed out  |
| i-0b666c22116dc6520     | 8000 | unhealthy | Target.Timeout | Request timed out  |

### 분류 (RCA 1차)
- **A) 포트/프로토콜·연결 문제:** Target.Timeout — ALB가 타깃 8000 포트에 연결했으나 5초 내 응답 없음. (연결 자체가 안 되거나, 앱이 응답하지 않음.)

---

## 3) API 인스턴스 내부 확인 (SSM)

**대상:** i-0b666c22116dc6520 (ASG 소속 1대)

### 명령 및 결과
- **docker ps:** 컨테이너 목록 빈 결과 (실행 중인 academy-api 컨테이너 없음).
- **docker logs academy-api --tail 200:** `Error response from daemon: No such container: academy-api`
- **ss -lntp \| grep 8000:** (출력 없음 — 8000 리스닝 프로세스 없음)
- **curl -v http://127.0.0.1:8000/health:** `Connection refused`

### 분류 (RCA 2차)
- **(1) 컨테이너 미기동** — UserData/이미지 pull/실행 실패 또는 컨테이너 시작 후 종료. 8000 포트 미리스닝.

---

## 4) Security Group / TargetGroup vs SSOT

### SSOT 기준
- **api.healthPath:** /health
- **포트:** 8000 (UserData/컨테이너)
- **api.securityGroupId / network.securityGroupApp:** sg-088fa3315c12754d0
- **network.vpcCidr:** 172.30.0.0/16

### TG 설정
- Port=8000, HealthCheckPath=/health, Matcher=200 → **SSOT와 일치.**

### sg-app (sg-088fa3315c12754d0) 인바운드
- 80, 443: 0.0.0.0/0
- **8000: 10.0.0.0/16** ← **불일치.** VPC CIDR는 172.30.0.0/16이므로 ALB(172.30.x.x)에서 EC2:8000으로 트래픽이 **차단**됨.

### ALB SG
- academy-v1-api-alb SG: sg-0405c1afe368b4e6b (ALB는 public subnet에 있음. ALB → EC2 헬스체크 시 EC2 sg-app 인바운드가 8000을 허용해야 함.)

### 분류 (RCA 3차)
- **SG 차단이 1차 원인:** sg-app 8000 인바운드가 10.0.0.0/16으로만 되어 있어, VPC 172.30.0.0/16 내 ALB가 EC2:8000에 도달하지 못함 → Target.Timeout.
- **컨테이너 미기동이 2차 원인:** 현재 인스턴스에서 앱이 떠 있지 않아, SG를 고쳐도 해당 인스턴스에서는 /health가 응답하지 않음. (신규 인스턴스/재기동 시 UserData로 컨테이너가 정상 기동되도록 해야 함.)

---

## 5) 확정 원인 (한 문장)

**sg-app의 8000 포트 인바운드가 10.0.0.0/16으로만 설정되어 있어, VPC CIDR 172.30.0.0/16인 ALB가 EC2:8000 헬스체크에 도달하지 못해 Target.Timeout이 발생하였고, 동시에 API 인스턴스에서 academy-api 컨테이너가 기동되지 않아 8000 포트가 열려 있지 않음.**

---

## 6) 조치 방향 (PHASE 2 반영)

1. **SG:** network.ps1에서 sg-app 8000 인바운드를 SSOT의 VpcCidr(172.30.0.0/16) 기준으로 보장. (신규 생성 시 VpcCidr 사용, 기존 SG에는 8000 from VpcCidr 규칙 추가.) → **적용 완료.** 배포 시 "SG ... added 8000 from 172.30.0.0/16 (SSOT)" 확인.
2. **TG:** 이미 SSOT와 일치하므로 변경 없음.
3. **컨테이너 기동:** UserData/이미지/실행 인자 점검 — 0.0.0.0:8000 리스닝, 실패 시 로그 남기도록 보강.

---

## 7) 조치 후 재검증 (이력)

- **Target health 변화:** SG 적용 후 기존 인스턴스는 Target.Timeout → instance refresh로 신규 2대(i-007504ce07a1b7c4a, i-0a5fef2a26e7c5132) 등록. 해당 2대는 **Target.FailedHealthChecks / Health checks failed** (Timeout 아님). 즉 ALB→EC2:8000 연결은 성공했으나 /health가 200이 아님.
- **SSM 신규 인스턴스(i-007504ce07a1b7c4a):** docker ps -a 빈 결과, curl 127.0.0.1:8000/health 실패. **컨테이너 미기동 상태 유지.**
- **결론:** SG 수정으로 네트워크 차단은 해소됨. 게이트 A 미달 원인은 **academy-api 컨테이너가 인스턴스에서 기동하지 않음**(이미지/ENV/DB 연결 등). 인스턴스 내 `/var/log/cloud-init-output.log`, `/var/log/academy-api-userdata.log` 확인 및 이미지 빌드/실행 조건 점검 필요.
