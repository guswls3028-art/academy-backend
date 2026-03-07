# Video 프로그래스바 검증 완료 리포트 (2025-03-07)

## 요약

프로그래스바 1% 고정 문제 원인 파악 및 수정 완료. API 재시작 포함 검증 반영.

---

## 원인

| 구간 | 상태 | 원인 |
|------|------|------|
| **Batch Worker → Redis** | ✅ 연결됨 | academy-redis-sg에 Batch SG 6379 인바운드 추가됨 |
| **API → Redis** | ❌ 연결 실패 | academy-redis-sg에 **App SG 6379 미허용** → API가 Redis progress 조회 불가 |

워커는 Redis에 진행률을 기록하지만, API가 Redis를 읽지 못해 progress API가 항상 0 반환 → 프론트 `Math.max(1, 0)` = 1% 고정.

---

## 적용한 수정

### 1. Redis SG (즉시 적용)

- **academy-redis-sg** (sg-0f4069135b6215cad)에 다음 인바운드 추가:
  - Batch SG (sg-0d5305dcafd3ccc4d) → TCP 6379
  - App SG (sg-03cf8c8f38f477687) → TCP 6379

### 2. IaC 반영 (redis.ps1)

- `Ensure-RedisSg6379FromWorkersAndApi`: Batch SG + App SG 6379 인바운드 보장
- 배포 시 자동 적용

### 3. API 재시작

- SSM Run Command로 `docker restart academy-api` 실행
- 인스턴스 i-02bc6583961ca07a9: Success

### 4. restart-api.ps1 스크립트

- `scripts/v1/restart-api.ps1` 추가
- Redis SG 변경 등으로 API 재시작이 필요할 때 사용

---

## 검증 체크리스트

| 항목 | 결과 |
|------|------|
| Redis SG Batch 6379 | ✅ |
| Redis SG App 6379 | ✅ |
| API 컨테이너 재시작 | ✅ |
| 배포 검증 (run-deploy-verification) | ✅ (WARNING: Drift 1건) |

---

## 사용자 확인 사항

1. **영상 새로 업로드** 후 프로그래스바가 증가하는지 확인
2. 기존 인코딩 중이던 영상은 재시작 전에 시작된 워커가 Redis에 기록했을 수 있으나, API가 재시작 전에는 읽지 못했음 → **새 영상**으로 테스트 권장

---

## 관련 문서

- `VIDEO-PROGRESS-REDIS-FLOW-REPORT.md` — 흐름 및 점검 사항
- `VIDEO-DELETE-CLEANUP-REPORT.md` — 영상 삭제 인프라 정리
