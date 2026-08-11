# Disaster Recovery Runbook

**Owner:** 운영자
**SSOT:** 본 문서. 변경 시 백업 정책 / RTO·RPO / 복구 절차 모두 갱신.

---

## 1. 현재 상태 (2026-08-11 readback)

```
RDS instance: academy-db
Engine:       PostgreSQL 15.17
Class:        db.t4g.medium
AZ:           ap-northeast-2b (Single-AZ)
Storage:      20 GB
Backup:       자동 7일 retention, 16:18-16:48 UTC (= 01:18-01:48 KST)
Boundary:     encrypted=true, public=false, deletionProtection=true
Network:      default-vpc-0831a2484f9b114c2 / sg-06cfb1f23372e2597
PITR:         LatestRestorableTime을 AWS에서 실행 직전에 조회
```

**확인 명령:**
```bash
aws rds describe-db-instances \
  --query 'DBInstances[*].{ID:DBInstanceIdentifier,Backup:BackupRetentionPeriod,MultiAZ:MultiAZ,AZ:AvailabilityZone}' \
  --region ap-northeast-2 --output table
```

---

## 2. RTO / RPO (목표)

| 시나리오 | RPO (잃을 수 있는 데이터) | RTO (복구 완료까지) |
|----------|------------------------|---------------------|
| **인스턴스 장애 (HW/네트워크 일시)** | 0 ~ 5분 (PITR) | 30분 (point-in-time restore) |
| **데이터 손상/실수 DELETE** | 1분 (PITR 가능 한도) | 2시간 (RDS PITR + 검증) |
| **AZ 장애 (Single-AZ는 영향 받음)** | 0 ~ 5분 (같은 리전 PITR 로그 사용 가능 시) | 2시간 (PITR/스냅샷 → 새 인스턴스 + 검증) |
| **Region 장애** | 사용 불가 (cross-region replica 없음) | 결정 필요 — 현재 미대응 |

**현재 risk:** Single-AZ + 7일 retention이다. AZ 장애는 Multi-AZ 자동
failover가 없어 새 인스턴스 복구와 앱 endpoint 변경이 필요하며, 리전 장애는
cross-region 사본이 없어 현재 복구할 수 없다. 자동 백업과 PITR 로그의 실제
복구 가능 여부는 사고/훈련 시작 시 `LatestRestorableTime`으로 다시 확인한다.

**개선 백로그 (사용자 결정 필요):**
- Multi-AZ 전환 (비용 ~2배 증가, but RTO 5분).
- Cross-region read replica (비용 + traffic. 학원 SaaS 규모에선 보류 가능).
- Retention 14일 (비용 미약, 권장).

---

## 3. 자동 백업 (현재 동작 중)

- **자동 스냅샷:** 매일 01:18-01:48 KST, 7일 보존 후 자동 삭제.
- **트랜잭션 로그:** 5분 단위 PITR 가능 (자동 백업 보존 기간 동안).
- **수동 스냅샷:** retention 정책 무시, 명시 삭제까지 보존. 큰 변경 직전 권장.

**수동 스냅샷 생성:**
```bash
aws rds create-db-snapshot \
  --db-instance-identifier academy-db \
  --db-snapshot-identifier academy-db-pre-$(date +%Y%m%d-%H%M) \
  --region ap-northeast-2
```

---

## 4. 복구 절차

### 4-A. PITR (Point-in-Time Restore) — 가장 흔한 경우

> 사용 시점: 실수 DELETE / 앱 버그로 데이터 손상 / 짧은 기간 롤백.

```bash
# 1) 복구할 시간 결정 — 손상 직전 (UTC).
TARGET_TIME="2026-04-30T07:30:00Z"

# 2) 새 인스턴스로 PITR (기존 인스턴스는 건드리지 않음).
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier academy-db \
  --target-db-instance-identifier academy-db-restore \
  --restore-time "$TARGET_TIME" \
  --db-instance-class db.t4g.medium \
  --region ap-northeast-2

# 3) 인스턴스가 available 될 때까지 대기 (~10-15분).
aws rds wait db-instance-available \
  --db-instance-identifier academy-db-restore --region ap-northeast-2

# 4) 데이터 검증 (별도 admin 접속, count + sanity check).
#    아래 5번 검증 체크리스트 실행.

# 5) 검증 OK면 endpoint 스왑:
#    옵션 A) 앱 SSM env DB_HOST를 신규 endpoint로 변경 + ASG refresh.
#    옵션 B) RDS rename (downtime 짧음, but proxy 재설정 필요).

# 6) 옛 인스턴스 5분 이상 idle 확인 후 manual snapshot → 삭제.
```

### 4-B. 스냅샷 복구 — Region/AZ 장애 또는 PITR 한도 외

```bash
# 1) 사용할 스냅샷 ID 선택.
aws rds describe-db-snapshots \
  --db-instance-identifier academy-db \
  --query 'DBSnapshots[?Status==`available`].{ID:DBSnapshotIdentifier,Time:SnapshotCreateTime}' \
  --output table --region ap-northeast-2

# 2) 새 인스턴스로 restore.
aws rds restore-db-instance-from-db-snapshot \
  --source-db-snapshot-identifier <SNAPSHOT_ID> \
  --target-db-instance-identifier academy-db-restore \
  --db-instance-class db.t4g.medium \
  --region ap-northeast-2

# 3) 동일하게 wait → 검증 → endpoint 스왑.
```

### 4-C. RDS Proxy 재구성

> 현재 운영 런타임은 SSM `DB_HOST`가 RDS instance endpoint를 직접 가리킨다.
> RDS Proxy는 2026-07-10 비용 절감 작업에서 제거했다. Proxy를 다시 도입한
> 뒤에만 아래 절차로 신규 인스턴스 target을 연결한다.

```bash
# Proxy target group의 DB_INSTANCE_IDENTIFIERS 업데이트.
aws rds modify-db-proxy-target-group \
  --db-proxy-name <DB_PROXY_NAME> \
  --target-group-name default \
  --connection-pool-config-info MaxConnectionsPercent=80 \
  --region ap-northeast-2

# 신규 instance를 target으로 등록 (또는 register-targets).
aws rds register-db-proxy-targets \
  --db-proxy-name <DB_PROXY_NAME> \
  --db-instance-identifiers academy-db-restore \
  --region ap-northeast-2

# 기존 instance target 해제.
aws rds deregister-db-proxy-targets \
  --db-proxy-name <DB_PROXY_NAME> \
  --db-instance-identifiers academy-db \
  --region ap-northeast-2
```

---

## 5. 검증 체크리스트 (복구 직후 필수)

복구된 DB에 대해 각 항목 확인. 하나라도 실패하면 endpoint 스왑 보류 + 재복구.

격리 복구 리허설은 `run-rds-restore-drill.ps1`이 다음 검증을 실행한다.

1. 복구 대상이 private, encrypted, Single-AZ이고 원본과 subnet group/보안
   그룹이 정확히 같은지 확인한다.
2. 운영 API 컨테이너의 private network 경로에서 **복구본에만** 현재
   migration을 적용한다.
3. `django_migrations`, `core_tenant`, `accounts_user`, `students_student`,
   `exams_exam`, `results_exam_result`, `fee_payment`,
   `messaging_schedulednotification`의 존재와 행 수만 수집한다. 사용자 행,
   tenant별 분포, 메시지 내용 등은 보고서에 기록하지 않는다.
4. 복구본의 pending migration이 0이고 운영 DB와 migration hash가 같으며
   `vector` extension이 존재하는지 확인한다.
5. 스냅샷과 현재 운영 DB 사이의 행 수 차이는 RPO 증거로 기록하되, 정상적인
   생성·삭제가 있을 수 있으므로 고정 ±비율로 성공/실패를 판정하지 않는다.
   대신 `django_migrations`, `core_tenant`, `accounts_user`가 비어 있으면 실패한다.

실제 사고의 endpoint 전환 전에는 위 자동 검증에 더해 영향 tenant의 최신
업무 데이터와 queue/provider 정합을 별도의 승인된 읽기 쿼리로 확인한다.

---

## 6. 사후 액션 (복구 후 24시간 이내)

1. **사고 보고서 작성** — 무엇을, 언제, 왜, 영향 범위, 복구 시간.
   - 위치: `docs/reports/incidents/incident-{YYYY-MM-DD}.md`.
2. **알림톡/메일/Slack로 학원장 안내** — 영향받은 테넌트만.
3. **opsauditlog 검증** — 복구 시점 이후 일관성. ID 시퀀스 충돌 없음.
4. **R2 storage 정합성** — DB와 R2 객체 키 mismatch 없는지 cleanup_orphan_video_storage 1회 dry-run.
5. **수동 스냅샷 생성** — 복구 직후 상태 보존.

---

## 7. 분기별 복구 리허설

목표: 운영 무영향 + 절차 검증.

GitHub Actions에서 RDS 인프라를 생성·삭제하는 것은
[`ops-prohibited.md`](ops-prohibited.md)에 따라 금지한다. 분기마다 승인된 운영자
AWS profile로 아래 수동 명령을 실행한다.

```powershell
# 읽기 계획: 원본 보호상태, 최신 자동 snapshot, class 지원 여부, 잔여 clone 확인
pwsh scripts/v1/run-rds-restore-drill.ps1 -AwsProfile <approved-operator> -Plan

# 실제 격리 restore → migrate/probe → exact-tag cleanup
pwsh scripts/v1/run-rds-restore-drill.ps1 -AwsProfile <approved-operator>
```

스크립트의 fail-closed 경계:

- 대상 이름은 `academy-db-drill-{UTC timestamp}-{run suffix}`로 생성하며 운영
  `academy-db`를 restore/delete 대상으로 사용할 수 없다.
- 시작 전에 같은 prefix의 잔여 DB가 하나라도 있으면 새 restore를 거부한다.
- cleanup 직전에 `Project=academy`, `Purpose=rds-restore-drill`, `SourceDb`,
  `RunId` 태그를 모두 다시 읽고 정확히 일치할 때만 삭제한다.
- 성공과 실패 모두 `C:\academy\_artifacts\rds-restore-drill\`에 PII 없는
  보고서를 남기며, cleanup 후 exact target 부재를 재확인한다.

리허설 시점에 본 runbook 자체도 검토 — 명령이 outdated되거나 절차가 바뀌었으면 갱신.

---

## 8. 참고

- [RDS Proxy 도입](../../infrastructure/infrastructure-optimization.md) — 2026-04-29 connection pool 만석 사고 대응.
- [Operations Baseline](../operations-baseline.md) — 일상 운영·헬스체크.
- [Incidents Runbook](incidents.md) — 사고 일반 대응.
