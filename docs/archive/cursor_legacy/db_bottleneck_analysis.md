# DB 병목 문제 분석 및 해결 방안

## 🔥 문제 현상

**"영상 업로드(=비디오 워커 인코딩 중)에 AI 워커 돌리면 고장남"**

## 📊 원인 분석

### 현재 구조
- **AI 워커**: t4g.medium (ASG, 최대 20개)
- **Video 워커**: t4g.medium (ASG, 최대 20개)
- **API 서버**: EC2
- **Redis**: ElastiCache
- **RDS**: **db.t4g.micro** (현재) → **db.t4g.small/medium** (권장)

### 자원 경쟁 패턴

#### 1. 비디오 워커의 DB 사용 (실제로는 최소화됨!)
- **인코딩 중**: CPU 100% 지속 (로컬 리소스)
- **Progress 기록**: Redis에만 기록 (DB 없음) ✅
  ```python
  # src/infrastructure/video/processor.py
  progress.record_progress(job_id, step, extra)  # Redis만 사용
  ```
- **DB 업데이트**: 시작 시 1번, 완료 시 1번만
  ```python
  # src/application/video/handler.py
  self._repo.mark_processing(video_id)      # 시작 시 1번
  self._repo.complete_video(...)            # 완료 시 1번
  ```
- **SELECT FOR UPDATE 사용**: Row-level lock
  ```python
  # academy/adapters/db/django/repositories_video.py
  Video.objects.select_for_update().filter(id=video_id).first()
  ```
- **특징**: DB 업데이트는 최소화되었지만, AI 워커가 RDS를 포화시키면 간단한 쿼리도 느려짐

#### 2. AI 워커의 DB 사용
- **Excel 파싱**: 대량의 학생 데이터 처리
  ```python
  # apps/domains/students/services/bulk_from_excel.py
  for row_index, raw in enumerate(students_data, start=1):
      student, created = get_or_create_student_for_lecture_enroll(...)
  ```
- **쿼리 패턴**: 각 학생마다 SELECT + INSERT/UPDATE
- **특징**: 긴 트랜잭션, 많은 쿼리, DB CPU 집약적

### 병목 발생 시나리오

```
Video 워커: 인코딩 중 (CPU 100%)
  ↓
중간중간 DB update (status/progress)
  ↓
AI 워커: Excel 파싱 시작
  ↓
대량 query 실행 (학생 N명 × SELECT + INSERT/UPDATE)
  ↓
RDS db.t4g.micro CPU 100%
  ↓
쿼리 지연 (latency 증가)
  ↓
Django timeout (DB_CONN_MAX_AGE=60 초과)
  ↓
작업 실패
```

## 🎯 확인해야 할 메트릭

### 1. RDS CPU 사용률
```powershell
# CloudWatch 메트릭 확인
aws cloudwatch get-metric-statistics `
  --namespace AWS/RDS `
  --metric-name CPUUtilization `
  --dimensions Name=DBInstanceIdentifier,Value=academy-db `
  --start-time (Get-Date).AddHours(-1).ToString("yyyy-MM-ddTHH:mm:ss") `
  --end-time (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss") `
  --period 300 `
  --statistics Average,Maximum `
  --region ap-northeast-2
```

**문제 지표:**
- CPU 80-100% 지속 → DB 병목 확실
- CPU 50-80% → 여유 있지만 주의 필요

### 2. DB 연결 수
```powershell
# CloudWatch 메트릭 확인
aws cloudwatch get-metric-statistics `
  --namespace AWS/RDS `
  --metric-name DatabaseConnections `
  --dimensions Name=DBInstanceIdentifier,Value=academy-db `
  --start-time (Get-Date).AddHours(-1).ToString("yyyy-MM-ddTHH:mm:ss") `
  --end-time (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss") `
  --period 300 `
  --statistics Average,Maximum `
  --region ap-northeast-2
```

**문제 지표:**
- max_connections 근접 → 연결 슬롯 부족
- db.t4g.micro: ~20-25개
- db.t4g.small: ~45-50개
- db.t4g.medium: ~90-100개

### 3. Video 인스턴스 CPU/Swap
```bash
# SSH 접속 후
top -bn1 | head -20
free -h
```

**문제 지표:**
- Swap 사용 → 메모리 부족
- CPU 100% 지속 → 정상 (인코딩 특성)

## 💡 해결 방안

### 즉시 조치 (1순위)

#### 1. RDS 인스턴스 크기 증가
```powershell
# db.t4g.medium으로 증가 (권장)
aws rds modify-db-instance `
  --db-instance-identifier academy-db `
  --db-instance-class db.t4g.medium `
  --apply-immediately `
  --region ap-northeast-2

# 또는 db.t4g.small (최소)
aws rds modify-db-instance `
  --db-instance-identifier academy-db `
  --db-instance-class db.t4g.small `
  --apply-immediately `
  --region ap-northeast-2
```

**효과:**
- CPU 용량 증가 (micro → small: 2배, small → medium: 2배)
- 최대 연결 수 증가
- 쿼리 처리 속도 향상

**비용:**
- db.t4g.micro → db.t4g.small: 약 2배
- db.t4g.small → db.t4g.medium: 약 2배

### 중기 조치 (2순위)

#### 2. AI 워커의 Bulk Create 최적화
현재: 각 학생마다 개별 쿼리
```python
# apps/domains/students/services/bulk_from_excel.py
for row_index, raw in enumerate(students_data, start=1):
    student, created = get_or_create_student_for_lecture_enroll(...)
```

개선: 배치 처리
```python
# Django의 bulk_create 사용
students_to_create = []
for row_index, raw in enumerate(students_data, start=1):
    # 검증만 수행
    student_data = validate_student_data(raw)
    if student_data:
        students_to_create.append(Student(**student_data))

# 배치로 일괄 생성
Student.objects.bulk_create(students_to_create, ignore_conflicts=True)
```

**효과:**
- 쿼리 수 감소 (N개 → 1개)
- 트랜잭션 시간 단축
- DB 부하 감소

#### 3. 비디오 워커의 DB 업데이트 빈도 줄이기
현재: 인코딩 단계마다 DB 업데이트
개선: Redis만 사용, 완료 시에만 DB 업데이트

```python
# 인코딩 중: Redis만 업데이트
progress.record_progress(job_id, step, extra)  # Redis만

# 완료 시: DB 업데이트
if step == "uploading" and percent == 100:
    video.save(update_fields=["status", "hls_path"])
```

**효과:**
- DB 쿼리 수 감소
- 인코딩 중 DB 부하 최소화

### 장기 조치 (3순위)

#### 4. Connection Pooling (PgBouncer)
- RDS Proxy 또는 PgBouncer 사용
- 연결 수 제한, 재사용
- DB_CONN_MAX_AGE=0 설정 (연결 즉시 반환)

#### 5. 비동기 처리 분리
- 비디오 워커와 AI 워커의 DB 업데이트를 큐로 분리
- 배치 처리로 일괄 업데이트

## 📈 모니터링 체크리스트

### 리프레시 완료 후 확인
- [ ] RDS CPU 사용률 < 80%
- [ ] DB 연결 수 < max_connections × 0.8
- [ ] 쿼리 지연 시간 < 100ms (평균)
- [ ] Video 워커와 AI 워커 동시 실행 시 정상 작동

### 정기 모니터링
- [ ] CloudWatch 알람 설정 (RDS CPU > 80%)
- [ ] DB 연결 수 알람 설정 (max_connections × 0.8)
- [ ] 쿼리 성능 모니터링 (Slow Query Log)

## 🎯 우선순위

1. **즉시**: RDS 인스턴스 크기 증가 (db.t4g.medium)
2. **1주일 내**: AI 워커 Bulk Create 최적화
3. **1개월 내**: 비디오 워커 DB 업데이트 빈도 줄이기
4. **장기**: Connection Pooling 도입

## 📝 참고

- RDS 인스턴스 변경 시 다운타임 발생 가능 (5-10분)
- `--apply-immediately` 없이 실행하면 다음 유지보수 시간에 적용 (다운타임 없음)
- 변경 후 CloudWatch 메트릭으로 모니터링 필수
