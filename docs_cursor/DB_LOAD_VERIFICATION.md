# DB 부하 감소 검증 가이드

## ✅ 현재 구현 상태 확인

### 1. Redis-only Progress 엔드포인트 구현 완료
- ✅ `VideoProgressView`: `/media/videos/{id}/progress/` (Redis-only)
- ✅ `JobProgressView`: `/api/v1/jobs/{job_id}/progress/` (Redis-only)
- ✅ 프론트엔드 폴링: `useWorkerJobPoller.ts`에서 Redis-only 엔드포인트 사용

### 2. 코드 레벨 확인 사항

#### ✅ 프론트엔드 폴링 경로 확인
```typescript
// useWorkerJobPoller.ts
- Excel: GET /jobs/{id}/progress/  ✅ Redis-only
- Video: GET /media/videos/{id}/progress/  ✅ Redis-only
```

#### ⚠️ 주의: Video Serializer는 여전히 DB 접근
```python
# apps/support/video/serializers.py
def get_encoding_progress(self, obj):
    # Video list/detail API에서 호출됨
    # 하지만 프론트엔드 폴링은 /progress/ 엔드포인트 사용하므로 영향 없음
```

---

## 📊 실제 DB 부하 확인 방법

### 방법 1: CloudWatch 메트릭 확인 (가장 정확)

#### 1.1 RDS CPUUtilization 확인
```bash
# AWS CLI로 확인
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name CPUUtilization \
  --dimensions Name=DBInstanceIdentifier,Value=<RDS_INSTANCE_ID> \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average,Maximum \
  --region ap-northeast-2
```

**기준:**
- **이전**: 비디오 인코딩 중 + AI 워커 동시 실행 시 → CPU 80-100%
- **목표**: 비디오 인코딩 중 + AI 워커 동시 실행 시 → CPU 30-50%

#### 1.2 DatabaseConnections 확인
```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name DatabaseConnections \
  --dimensions Name=DBInstanceIdentifier,Value=<RDS_INSTANCE_ID> \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average,Maximum \
  --region ap-northeast-2
```

**기준:**
- **이전**: 폴링으로 인한 연결 수 증가
- **목표**: 폴링 제거로 연결 수 감소 (워커 작업 시에만 증가)

#### 1.3 ReadLatency / WriteLatency 확인
```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name ReadLatency \
  --dimensions Name=DBInstanceIdentifier,Value=<RDS_INSTANCE_ID> \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average,Maximum \
  --region ap-northeast-2
```

**기준:**
- **이전**: 폴링으로 인한 ReadLatency 증가
- **목표**: ReadLatency 감소 (특히 진행률 조회 관련)

---

### 방법 2: Django 로그 분석 (실제 쿼리 패턴 확인)

#### 2.1 API 서버 로그에서 `/progress/` 엔드포인트 확인
```bash
# API 서버에서
grep "GET.*progress" /var/log/django/api.log | wc -l
# 폴링 요청 수 확인

grep "SELECT.*video" /var/log/django/api.log | grep -i progress | wc -l
# DB 쿼리 수 확인 (0에 가까워야 함)
```

#### 2.2 Django Debug Toolbar 또는 django-silk 사용
```python
# settings.py에 추가
INSTALLED_APPS = [
    ...
    'silk',  # 프로파일링 도구
]

MIDDLEWARE = [
    ...
    'silk.middleware.SilkyMiddleware',
]
```

**확인 사항:**
- `/media/videos/{id}/progress/` 엔드포인트에서 DB 쿼리 수: **0개여야 함**
- `/api/v1/jobs/{job_id}/progress/` 엔드포인트에서 DB 쿼리 수: **0개여야 함**

---

### 방법 3: PostgreSQL 직접 쿼리 분석

#### 3.1 pg_stat_statements 확장 사용
```sql
-- 활성화 확인
SELECT * FROM pg_extension WHERE extname = 'pg_stat_statements';

-- 가장 많이 실행된 쿼리 확인
SELECT 
    query,
    calls,
    total_exec_time,
    mean_exec_time,
    max_exec_time
FROM pg_stat_statements
WHERE query LIKE '%video%' OR query LIKE '%progress%'
ORDER BY calls DESC
LIMIT 20;
```

#### 3.2 현재 실행 중인 쿼리 확인
```sql
SELECT 
    pid,
    usename,
    application_name,
    state,
    query,
    query_start,
    now() - query_start AS duration
FROM pg_stat_activity
WHERE state = 'active'
  AND query NOT LIKE '%pg_stat_activity%'
ORDER BY query_start;
```

**확인 사항:**
- `SELECT ... FROM video ... WHERE status = 'PROCESSING'` 같은 진행률 조회 쿼리가 **사라져야 함**

---

### 방법 4: Redis 모니터링 (Redis 부하 확인)

#### 4.1 Redis 명령 통계 확인
```bash
# Redis CLI에서
redis-cli INFO stats | grep total_commands_processed
redis-cli INFO stats | grep instantaneous_ops_per_sec
```

#### 4.2 Redis 키 확인
```bash
# 진행률 관련 키 확인
redis-cli KEYS "progress:*"
redis-cli KEYS "video:status:*"
redis-cli KEYS "job:status:*"
```

---

## 🎯 검증 시나리오

### 시나리오 1: 비디오 업로드 + AI 워커 동시 실행
1. 비디오 업로드 시작 (인코딩 진행 중)
2. AI 워커 엑셀 파싱 작업 시작
3. **CloudWatch에서 RDS CPUUtilization 확인**
   - **목표**: CPU 50% 이하 유지
   - **이전**: CPU 80-100% → 타임아웃 발생

### 시나리오 2: 다중 사용자 동시 폴링
1. 10명의 사용자가 각각 비디오 업로드
2. 각 사용자의 브라우저에서 진행률 폴링 (1초마다)
3. **DatabaseConnections 확인**
   - **목표**: 연결 수 증가 없음 (Redis만 사용)
   - **이전**: 사용자당 1개씩 연결 증가

### 시나리오 3: 엑셀 대량 등록 (5000명)
1. 엑셀 파일 업로드 (5000명 학생)
2. AI 워커 처리 시작
3. **DB 쿼리 수 확인**
   - **목표**: N+1 쿼리 제거 (bulk_create 사용)
   - **이전**: 5000개 이상의 개별 INSERT

---

## 📈 성공 기준

### ✅ Redis Progress 전환 성공 기준
- [ ] `/progress/` 엔드포인트에서 DB 쿼리 0개
- [ ] CloudWatch ReadLatency 감소 (폴링 관련)
- [ ] DatabaseConnections 감소 (폴링 제거)

### ✅ Excel Bulk 최적화 성공 기준
- [ ] 5000명 등록 시 쿼리 수 < 100개 (이전: 5000개 이상)
- [ ] 처리 시간 감소 (50% 이상)
- [ ] DB CPU 부하 감소

### ✅ 전체 시스템 안정성 기준
- [ ] 비디오 인코딩 + AI 워커 동시 실행 시 RDS CPU < 50%
- [ ] 타임아웃 오류 0건
- [ ] 연결 슬롯 부족 오류 0건

---

## 🔍 문제 발견 시 체크리스트

### DB 부하가 여전히 높다면:
1. [ ] 프론트엔드가 `/progress/` 엔드포인트를 사용하는지 확인
2. [ ] 기존 폴링 코드가 남아있는지 확인 (레거시 API 호출)
3. [ ] Video Serializer의 `get_encoding_progress`가 다른 곳에서 호출되는지 확인
4. [ ] Redis 키가 제대로 생성되는지 확인 (`redis-cli KEYS "progress:*"`)
5. [ ] 워커가 `record_progress`를 제대로 호출하는지 확인

### Redis 부하가 높다면:
1. [ ] TTL 설정 확인 (완료된 작업은 TTL 없음)
2. [ ] 키 네임스페이스 확인 (tenant_id 포함)
3. [ ] Redis 메모리 사용량 확인 (`redis-cli INFO memory`)

---

## 📝 모니터링 스크립트 예시

```bash
#!/bin/bash
# check_db_load.sh

RDS_INSTANCE="your-rds-instance-id"
REGION="ap-northeast-2"

echo "=== RDS CPU Utilization (Last 1 hour) ==="
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name CPUUtilization \
  --dimensions Name=DBInstanceIdentifier,Value=$RDS_INSTANCE \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average,Maximum \
  --region $REGION \
  --output table

echo ""
echo "=== Database Connections (Last 1 hour) ==="
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name DatabaseConnections \
  --dimensions Name=DBInstanceIdentifier,Value=$RDS_INSTANCE \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average,Maximum \
  --region $REGION \
  --output table
```

---

## 🚨 즉시 확인 가능한 방법

### 1. 브라우저 개발자 도구 확인
1. 비디오 업로드 중 Network 탭 열기
2. `/media/videos/{id}/progress/` 요청 확인
3. **응답 시간**: Redis 조회이므로 < 10ms여야 함
4. **DB 쿼리**: 서버 로그에서 확인 (0개여야 함)

### 2. API 서버 로그 실시간 확인
```bash
# API 서버에서
tail -f /var/log/django/api.log | grep progress
# DB 쿼리 로그가 없어야 함
```

### 3. Redis CLI 실시간 확인
```bash
# 진행률 키 확인
watch -n 1 'redis-cli KEYS "progress:*" | wc -l'

# 특정 비디오 진행률 확인
redis-cli GET "progress:video:123:tenant:1"
```

---

## ✅ 최종 확인 체크리스트

- [ ] CloudWatch에서 RDS CPUUtilization 확인 (목표: 50% 이하)
- [ ] CloudWatch에서 DatabaseConnections 확인 (폴링 제거로 감소)
- [ ] `/progress/` 엔드포인트에서 DB 쿼리 0개 확인
- [ ] 프론트엔드가 Redis-only 엔드포인트 사용 확인
- [ ] 비디오 + AI 워커 동시 실행 시 안정성 확인
- [ ] 엑셀 대량 등록 시 쿼리 수 감소 확인
