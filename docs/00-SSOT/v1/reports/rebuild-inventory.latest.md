# V1 Stateless Compute 재구축 — 인벤토리 (스냅샷)

**Generated:** (run-rebuild-inventory.ps1 실행 시 갱신)  
**리전:** ap-northeast-2  
**SSOT:** docs/00-SSOT/v1/params.yaml  

## 1) 유지 대상 확인 (데이터 레이어)
- **RDS**: academy-db (삭제 금지)
- **Redis**: academy-v1-redis (삭제 금지)
- **SSM**: `/academy/api/env`, `/academy/workers/env`, `/academy/rds/master_password` (삭제 금지)
- **ECR**: academy-* repos (삭제 금지)

## 2) 재구축 대상 (compute 레이어)

### API
- ALB: `academy-v1-api-alb`
- TG: `academy-v1-api-tg` (health: `/healthz`, port 8000)
- ASG: `academy-v1-api-asg` (목표 1/1/2)
- LT: `academy-v1-api-lt`

### Workers
- AI ASG/LT: `academy-v1-ai-worker-asg` / `academy-v1-ai-worker-lt` (목표 1/1/5)
- Messaging ASG/LT: `academy-v1-messaging-worker-asg` / `academy-v1-messaging-worker-lt` (목표 1/1/3)

### Batch / Ops
- CE/Queue/JobDef/Ops CE/Queue/JobDef (minvCpus=0)

### EventBridge
- `academy-v1-reconcile-video-jobs`, `academy-v1-video-scan-stuck-rate` (targets 포함)

### Network / 비용 리소스
- NAT Gateway: (목표 0)
- EIP: (목표 0 — 사용자 관리 기준)
- Security Groups: (목표 ≤ 8)

---

## 3) Actual Snapshot (자동 채움 영역)

> 아래는 자동 스냅샷 섹션. `scripts/v1/run-rebuild-inventory.ps1` 실행 결과로 채운다.

### EC2 running (Project=academy)
| InstanceId | Name | SubnetId | PublicIp | PrivateIp |
|------------|------|----------|----------|-----------|
| (auto) | | | | |

### ASG
| Name | Min | Desired | Max | Subnets |
|------|-----|---------|-----|---------|
| (auto) | | | | |

### ALB/TG health
| ALB | TG | HealthPath | Healthy/Total |
|-----|----|------------|--------------|
| (auto) | | | |

### Batch
| Type | Name | Status/State | Notes |
|------|------|--------------|------|
| (auto) | | | |

### EventBridge
| Rule | State | Targets |
|------|-------|---------|
| (auto) | | |

### NAT/EIP/Routes
| Item | Value | Notes |
|------|-------|------|
| (auto) | | |

### Security Groups (VPC)
| GroupId | GroupName | ENI count |
|---------|-----------|-----------|
| (auto) | | |

