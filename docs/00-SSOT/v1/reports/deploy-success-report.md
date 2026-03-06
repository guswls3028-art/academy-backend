# Academy v1 배포 성공 보고서

**생성일시:** 2026-03-06  
**Region:** ap-northeast-2  
**상태:** DEPLOY_SUCCESS

---

## 1. FACT REPORT (확인된 사실)

### 리포지토리/스크립트
- API/Workers: `natEnabled=false` 시 Public 서브넷 사용
- SSM 대기: `SkipApiSSMWait`(MinimalDeploy 시 자동)로 생략 가능
- ALB health path: `/healthz`
- sg-app: 80/443 from 0.0.0.0/0, 8000 from VpcCidr(172.30.0.0/16)

### AWS 실제 상태 (검증 시점)
- API 인스턴스(i-0dc75ae87bd5ec68b): Public 서브넷(2c), SSM Online, Docker 컨테이너 healthy, 8000 리스닝
- Target health: **healthy**
- API ASG / AI Worker ASG / Messaging Worker ASG: 각 1대 InService
- Batch CE(academy-v1-video-batch-ce): VALID, ENABLED
- Batch Queue(academy-v1-video-batch-queue): ENABLED, VALID

### 확인된 블로커 및 해결
1. **ALB SG**: 기본 SG가 80/443 인바운드 없음 → 0.0.0.0/0 추가로 해결
2. **Target health unhealthy**: 컨테이너 기동 지연 → 시간 경과 후 healthy 전환

---

## 2. CHANGES MADE

### 이전 세션에서 적용된 변경
- Private RT에 IGW 라우트 추가
- API ASG → Target Group 연결
- api.ps1 / asg_ai.ps1 / asg_messaging.ps1: natEnabled=false 시 Public 서브넷 사용
- network.ps1: natEnabled=false 시 private RT에 0.0.0.0/0 → IGW 라우트 추가

### 이번 세션에서 적용된 변경
- **ALB SG**: sg-0405c1afe368b4e6b에 80, 443 from 0.0.0.0/0 수동 추가
- **alb.ps1**: `Ensure-ALBSecurityGroup` 함수 추가 — ALB SG에 80/443 from 0.0.0.0/0 보장

---

## 3. EXACT DEPLOY COMMAND USED

```powershell
pwsh -File scripts/v1/deploy.ps1 -Env prod -MinimalDeploy -SkipNetprobe -AwsProfile default
```

---

## 4. VERIFICATION RESULTS

| 항목 | 결과 |
|------|------|
| API ASG | 1 running |
| AI Worker ASG | 1 running |
| Messaging Worker ASG | 1 running |
| ALB | academy-v1-api-alb 존재 |
| Target Group | academy-v1-api-tg |
| Target health | healthy |
| Batch CE (video standard) | VALID, ENABLED |
| Batch Queue (video standard) | ENABLED, VALID |
| ALB /healthz | `{"status": "ok", "service": "academy-api"}` |

---

## 5. FINAL STATUS

**DEPLOY_SUCCESS**

- API가 ALB를 통해 정상 응답
- AI/Messaging 워커 기동
- Video Standard Batch 경로 사용 가능
- 배포는 `deploy.ps1 -MinimalDeploy -SkipNetprobe`로 재현 가능
