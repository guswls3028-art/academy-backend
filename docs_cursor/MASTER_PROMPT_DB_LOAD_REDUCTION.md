# MASTER EXECUTION — 10K 대비 DB 부하 0 아키텍처 설계 (CTO ALIGNMENT MODE)

## 🎯 역할 정의

너는 단순 코드 생성기가 아니라, 내 프로젝트 전체를 실제 코드 기준으로 분석하는 **수석 백엔드 아키텍트**다.

**절대 추측 금지. 실제 코드 기반으로만 분석.**
- grep / 파일 경로 / 실제 모델 구조 기준으로 판단
- 일반론이나 "보통은" 같은 말 금지
- 실제 코드 확인 없이 결론 금지

---

## 🎯 최종 목표

**500 → 3K → 10K 사용자까지 구조를 갈아엎지 않고 확장 가능한 아키텍처 설계**

핵심 원칙:
- ✅ DB 폴링 0 (진행 상태는 Redis-only)
- ✅ Excel은 row-by-row 금지 (Bulk only)
- ✅ Worker 무한 확장 금지 (Max concurrency limit)
- ✅ DB는 영구 저장소, Redis는 상태 스트림/캐시
- ✅ 10K까지 구조 변경 없이 인스턴스 확장만으로 대응

---

## 📋 1. 현재 설계 방향 (이 기준을 절대 벗어나지 말 것)

### Redis-only Progress 구조

**진행 중 작업:**
- `status` → Redis
- `progress` → Redis
- `step` → Redis
- `error` → Redis
- **DB 조회 금지**

**완료 시:**
- DB 영구 저장 (필수)
- Redis에 완료 상태 캐시
- 완료 TTL: 없음 (권장) 또는 24시간 (비용 방어 모드)
- 진행 중 TTL: 6시간 (슬라이딩 갱신)

**API 설계:**
- `GET /media/videos/{id}/progress/` → Redis-only endpoint (신규)
- `GET /api/v1/jobs/{job_id}/progress/` → Redis-only endpoint (신규)
- 기존 detail endpoint는 DB 기반 유지
- **진행 중 폴링은 progress endpoint만 사용**

**프론트엔드 전략:**
- 진행 중: progress endpoint만 폴링
- 완료 감지: detail endpoint 1회 호출 후 폴링 종료
- 적응형 폴링 간격 (0~10초: 1초, 10~60초: 2초, 60초+: 3초)

---

## 📋 2. Redis 키 설계 (멀티테넌트 강제)

**모든 키는 다음 형식 (Tenant 네임스페이스 필수):**

```
tenant:{tenant_id}:video:{video_id}:status
tenant:{tenant_id}:video:{video_id}:progress
tenant:{tenant_id}:job:{job_id}:status
tenant:{tenant_id}:job:{job_id}:progress
```

**tenant namespace 누락 시 실패로 간주.**

**TTL 정책:**
- 진행 중 (PROCESSING): 6시간 (슬라이딩 갱신)
- 완료 (READY/DONE/FAILED): TTL 없음 (권장) 또는 24시간
- Redis eviction policy: `volatile-lru` 설정

**Result 저장 정책:**
- result payload 10KB 이상은 Redis 저장 금지
- 대용량은 DB만 저장, Redis엔 status만 저장

---

## 📋 3. Excel 대량 처리 정책 (절대 원칙)

### 금지 사항:
- ❌ `get_or_create` 루프
- ❌ row-by-row transaction
- ❌ Q OR 조건 500개 연결 (비효율적 execution plan)

### 필수 사항:
- ✅ Composite index 활용: `(tenant_id, name, parent_phone)`
- ✅ Tuple IN 방식 또는 임시테이블 방식
- ✅ `bulk_create` 사용
- ✅ `bulk_update` 사용
- ✅ Chunked transaction (200~500 단위)
- ✅ `SELECT *` 금지 (최소 컬럼만 조회)

### 10K 대비 설계:
- 5000 row 업로드도 버틸 수 있는 구조
- 임시 테이블 전략 옵션 설계 포함 (현재는 필요 없지만 문서화)

### 구현 전략:
1. **배치 조회**: 기존 학생, 삭제된 학생 일괄 조회 (Tuple IN)
2. **Bulk Create**: 신규 학생 일괄 생성
3. **Bulk Update**: User FK 연결, TenantMembership 일괄 생성
4. **Chunked Transaction**: 200개 단위로 나눠서 처리

---

## 📋 4. Worker 설계 원칙

### 동시성 제어:
- SQS 기반 유지
- **Max concurrency hard limit 존재**
- ASG max size = concurrency limit
- 예: AI worker max 5, Video worker max 3, Messaging worker max 5

### DB Connection 관리:
- Worker 1개당 DB connection 수 계산 필요
- `DB_CONN_MAX_AGE = 15` 권장
- Connection saturation 방지 전략 포함

### 10K 대비:
- SQS depth 기반 scaling
- BUT Max concurrency hard limit 존재
- Worker 폭증 방지

---

## 📋 5. DB 10K 대비 설계

### 필수 인덱스 설계:

**students 테이블:**
```sql
CREATE INDEX idx_student_tenant_name_phone
ON students (tenant_id, name, parent_phone)
WHERE deleted_at IS NULL;

CREATE INDEX idx_student_tenant_name_phone_deleted
ON students (tenant_id, name, parent_phone)
WHERE deleted_at IS NOT NULL;
```

**aijob 테이블:**
```sql
CREATE INDEX idx_aijob_tenant_status
ON aijob (tenant_id, status);

CREATE INDEX idx_aijob_tenant_job_id
ON aijob (tenant_id, job_id);
```

**video 테이블:**
```sql
CREATE INDEX idx_video_tenant_status
ON video (tenant_id, status);

CREATE INDEX idx_video_session_status
ON video (session_id, status);
```

### 쿼리 최적화:
- `SELECT *` 제거
- 최소 컬럼 조회 전략
- Read/Write repository 논리적 분리 구조 설계 (10K에서 Reader/Writer Endpoint 분리 준비)

---

## 📋 6. Redis 운영 안정성

### 메모리 관리:
- result payload 10KB 이상은 Redis 저장 금지
- eviction policy: `volatile-lru` 설정
- 메모리 증가 시 안전 전략 포함

### TTL 관리:
- 진행 중: 슬라이딩 갱신 (exists 체크 후 expire)
- 완료: TTL 없음 또는 24시간
- TTL 정책 근거 포함

### 모니터링:
- CloudWatch: `used_memory > 70%` 알람
- Eviction 발생 시 알람

---

## 📋 7. 산출물 요구 형식

다음 순서로 작성하라:

1. **현재 코드 구조 분석 결과**
   - 실제 파일 경로 기준
   - 현재 구현 상태
   - 병목 지점 식별

2. **병목 예상 지점 (500 / 3K / 10K 단계별)**
   - 각 단계에서 터질 수 있는 지점
   - 수치 기반 예측

3. **수정 필요 파일 리스트**
   - 파일 경로 + 함수명 + 수정 내용
   - 우선순위 포함

4. **Redis 구조 정리안**
   - 키 설계 (Tenant 네임스페이스 포함)
   - TTL 정책
   - 헬퍼 함수 설계

5. **Excel bulk 최종 설계안**
   - 배치 조회 메서드
   - Bulk Create 함수
   - Chunked transaction 로직
   - 10K 대비 임시테이블 옵션

6. **Worker concurrency 설계안**
   - Max limit 설정
   - DB connection 수 계산
   - ASG 설정 제안

7. **DB index 제안 SQL**
   - 위 인덱스 SQL 포함
   - 실행 순서 제안

8. **10K 확장 시 인프라 변경 없이 가능한지 여부 판단**
   - 구조 변경 필요 여부
   - 인스턴스 확장만으로 가능 여부

9. **Aurora 필요 기준선 제시**
   - 언제 Aurora로 전환해야 하는지
   - 수치 기반 기준

10. **위험 요소 및 보완 전략**
    - 잠재적 문제점
    - 해결 방안

---

## 📋 8. 절대 금지 사항

- ❌ 추측 금지
- ❌ 일반론 금지
- ❌ "보통은" 같은 말 금지
- ❌ 실제 코드 확인 없이 결론 금지

**모든 판단은:**
- 파일 경로 + 실제 코드 + 실행 흐름 기준으로 작성하라.

---

## 📋 9. 최종 목표

**"이 설계는 10K에서 갈아엎지 않아도 된다"**

라는 판단이 나올 때까지 구조를 정렬하라.

### 완성되면 평가:

- **구조 안정성 점수**: /10
- **확장성 점수**: /10
- **비용 대비 효율 점수**: /10

각각 10점 만점으로 평가하라.

---

## 📋 10. 참고 문서

- `docs_cursor/db_load_reduction_design_final.md`: 현재 설계 문서
- `apps/support/video/services/sqs_queue.py`: 비디오 워커 저장 로직
- `apps/domains/students/services/bulk_from_excel.py`: 엑셀 대량 처리
- `academy/adapters/db/django/repositories_ai.py`: AI Job 저장 로직
- `apps/support/video/views/video_views.py`: 비디오 조회 API
- `apps/domains/ai/views/job_status_view.py`: Job 상태 조회 API

---

## 🚀 실행 시작

위 요구사항을 바탕으로 실제 코드를 분석하고, 10K 대비 아키텍처를 설계하라.

**모든 분석은 실제 파일을 읽고, 실제 코드를 확인한 후 진행하라.**
