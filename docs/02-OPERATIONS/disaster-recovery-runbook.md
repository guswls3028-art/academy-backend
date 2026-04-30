# Disaster Recovery Runbook

**Owner:** 운영자
**SSOT:** 본 문서. 변경 시 백업 정책 / RTO·RPO / 복구 절차 모두 갱신.

---

## 1. 현재 상태 (2026-04-30 기준)

```
RDS instance: academy-db
Engine:       PostgreSQL 15.16
Class:        db.t4g.large
AZ:           ap-northeast-2b (Single-AZ)
Storage:      20 GB
Backup:       자동 7일 retention, 16:18-16:48 UTC (= 01:18-01:48 KST)
Snapshots:    8 automated + 1 manual (4-30 시점)
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
| **AZ 장애 (Single-AZ는 영향 받음)** | 24시간 (마지막 자동 스냅샷) | 4시간 (스냅샷 → 새 인스턴스) |
| **Region 장애** | 사용 불가 (cross-region replica 없음) | 결정 필요 — 현재 미대응 |

**현재 risk:** Single-AZ + 7일 retention. 가장 큰 갭: AZ 장애 시 RPO 24시간. 학원 결제·메시지 데이터는 RPO 1시간 이내가 적정.

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
  --db-instance-class db.t4g.large \
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
  --db-instance-class db.t4g.large \
  --region ap-northeast-2

# 3) 동일하게 wait → 검증 → endpoint 스왑.
```

### 4-C. RDS Proxy 재구성

> 운영은 RDS Proxy(2026-04-29 도입)로 연결. 신규 인스턴스 endpoint를 Proxy target으로 등록 필요.

```bash
# Proxy target group의 DB_INSTANCE_IDENTIFIERS 업데이트.
aws rds modify-db-proxy-target-group \
  --db-proxy-name academy-db-proxy \
  --target-group-name default \
  --connection-pool-config-info MaxConnectionsPercent=80 \
  --region ap-northeast-2

# 신규 instance를 target으로 등록 (또는 register-targets).
aws rds register-db-proxy-targets \
  --db-proxy-name academy-db-proxy \
  --db-instance-identifiers academy-db-restore \
  --region ap-northeast-2

# 기존 instance target 해제.
aws rds deregister-db-proxy-targets \
  --db-proxy-name academy-db-proxy \
  --db-instance-identifiers academy-db \
  --region ap-northeast-2
```

---

## 5. 검증 체크리스트 (복구 직후 필수)

복구된 DB에 대해 각 항목 확인. 하나라도 실패하면 endpoint 스왑 보류 + 재복구.

```bash
# A. 행 수 sanity (운영 시점과 큰 차이 없어야 함)
psql -h <RESTORED_ENDPOINT> -U academy -c "
SELECT 'tenants' AS tbl, count(*) FROM tenant
UNION ALL SELECT 'users',         count(*) FROM auth_user
UNION ALL SELECT 'fee_payments',  count(*) FROM fee_payment
UNION ALL SELECT 'exam_results',  count(*) FROM exam_result
UNION ALL SELECT 'matchup_docs',  count(*) FROM matchup_document;
"

# B. 최근 1시간 INSERT 검증 (PITR 시점 직전 데이터까지 반영)
psql -h <RESTORED_ENDPOINT> -U academy -c "
SELECT count(*) AS recent_attendance
FROM attendance
WHERE created_at > now() - interval '1 hour';
"

# C. tenant 격리 — 임의 tenant_id에 해당 row만 조회되는지 확인.
psql -h <RESTORED_ENDPOINT> -U academy -c "
SELECT tenant_id, count(*) FROM exam_result
GROUP BY tenant_id
ORDER BY tenant_id LIMIT 10;
"

# D. 마이그레이션 상태 — 운영과 동일해야 함.
python manage.py showmigrations --plan | tail -20

# E. 알림톡/SMS 발송 큐 정합 — 미전송 row가 비정상적으로 늘어나 있지 않은지.
psql -h <RESTORED_ENDPOINT> -U academy -c "
SELECT status, count(*) FROM message_send_log
WHERE created_at > now() - interval '6 hours'
GROUP BY status;
"
```

검증 OK 기준:
- A: 운영 row 수와 ±5% 이내.
- B: 1시간 윈도우에 신규 attendance > 0 (정상 운영 중이었을 경우).
- C: 모든 tenant_id 분포가 운영과 동일.
- D: 마이그레이션 적용 상태 동일.
- E: 비정상 'failed' 누적 없음.

---

## 6. 사후 액션 (복구 후 24시간 이내)

1. **사고 보고서 작성** — 무엇을, 언제, 왜, 영향 범위, 복구 시간.
   - 위치: `_artifacts/reports/incident-{YYYY-MM-DD}.md`.
2. **알림톡/메일/Slack로 학원장 안내** — 영향받은 테넌트만.
3. **opsauditlog 검증** — 복구 시점 이후 일관성. ID 시퀀스 충돌 없음.
4. **R2 storage 정합성** — DB와 R2 객체 키 mismatch 없는지 cleanup_orphan_video_storage 1회 dry-run.
5. **수동 스냅샷 생성** — 복구 직후 상태 보존.

---

## 7. 분기별 복구 리허설 (권장)

목표: 운영 무영향 + 절차 검증.

```
Q1 / Q2 / Q3 / Q4 의 중간 토요일 03:00 KST (peak 회피).
1. 가장 최근 자동 snapshot으로 academy-db-drill 인스턴스 restore.
2. 위 §5 검증 체크리스트 실행.
3. 결과를 _artifacts/reports/dr-drill-{date}.md 에 기록.
4. 인스턴스 즉시 삭제.
```

리허설 시점에 본 runbook 자체도 검토 — 명령이 outdated되거나 절차가 바뀌었으면 갱신.

---

## 8. 참고

- [RDS Proxy 도입](../00-SSOT/v1.1.0/INFRASTRUCTURE-OPTIMIZATION.md) — 2026-04-29 connection pool 만석 사고 대응.
- [Operations Baseline](OPERATIONS-BASELINE.md) — 일상 운영·헬스체크.
- [Incidents Runbook](../00-SSOT/v1.1.0/RUNBOOK-INCIDENTS.md) — 사고 일반 대응.
