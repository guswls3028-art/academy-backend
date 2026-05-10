# 백엔드 구조 리팩토링 보고서 — 2026-04-13

**커밋:** `7845d9af`
**규모:** 1011 files, +24,640 / -67,086 (순감소 42,446줄)
**운영 검증:** API 200 OK, 전 도메인 실데이터 반환, 워커 정상

---

## 변경 요약

### 1. Docker 최적화
- ai-worker-cpu: pip install 3 RUN → 1 RUN (레이어 2개 감소)
- docker-compose.yml: YAML 앵커로 DRY (403줄→258줄)
- 서비스별 .dockerignore 삭제 (빌드 컨텍스트 루트만 적용되므로 dead code)
- build.sh/build.ps1: 레거시 제거, 개별 빌드 지원 (`./build.sh api`)
- docker/ai-worker/ (레거시 flexible 모드) 삭제
- 헬스체크: `pgrep -f python` → `kill -0 1` (python:3.11-slim에 procps 없음)

### 2. 헥사고날 아키텍처 통합 (src/ → academy/)
- `src/application/ports/` → `academy/application/ports/` (7개 포트 인터페이스)
- `src/application/services/` → `academy/application/services/`
- `src/application/video/` → `academy/application/video/`
- `src/infrastructure/` → `academy/adapters/` (cache, video, storage, ai, db)
- 19개 파일 import 경로 `from src.` → `from academy.` 치환
- `src/` 디렉토리 완전 삭제
- Dockerfile: `COPY src ./src` 제거

### 3. 대형 파일 분할

| 원본 | 줄수 | → 분할 수 | 방식 |
|------|------|----------|------|
| students/views.py | 2,290 | 5파일 | tag, student, registration, password, credential |
| clinic/views.py | 1,391 | 6파일 | session, participant, test, submission, settings, idcard |
| core/views.py | 1,149 | 9파일 | auth, program, profile, attendance, expense, job_progress, tenant_* |
| messaging/views.py | 1,382 | 5파일 | info, log, template, send, config |
| messaging/services.py | 824 | 5파일 | solapi_client, queue, url_helpers, notification, registration |
| staffs/views.py | 877 | 8파일 | staff, work_record, expense_record, payroll_snapshot, work_type 등 |
| community/api/views.py | 819 | 8파일 | post, admin, block_type, template, scope_node, platform_inbox 등 |

모든 분할에서 `__init__.py` 재export → 기존 import 100% 호환.

### 4. 도메인 폴더 표준화
- 22개 도메인 전부 `services/`, `tests/` 디렉토리 보유
- 단일 `services.py` → `services/` 디렉토리 변환 (enrollment, fees, inventory, parents)

### 5. 데드코드 제거
- `libs/`: redis_client, tenant_util, observability, s3_client, scripts (전부 미import)
- `apps/`: storage (고아), api/index.ts (TypeScript in backend), worker/ai_worker/apps/ (고스트)
- root: `frontend/` (0-byte VideoPlayer.tsx), `package.json` + `node_modules/`
- 손상된 pyc, 0-byte 미사용 파일 다수

---

## 운영 검증 결과

| 항목 | 결과 |
|------|------|
| API Health | ✅ 200 OK, DB connected |
| JWT 인증 | ✅ 토큰 발급 정상 |
| Students API | ✅ count=16, 실데이터 반환 |
| Lectures API | ✅ count=1 |
| Clinic Sessions | ✅ count=39 |
| Community Posts | ✅ count=37 |
| Staffs | ✅ count=18 |
| Messaging | ✅ 정상 |
| ECR | ✅ sha-7845d9af + latest |
| Workers | ✅ messaging, ai-worker 프로세스 정상 |
| SQS | ✅ idle |
| Batch/EventBridge | ✅ 3큐 + 5룰 ENABLED |

---

## 남은 과제

| 항목 | 심각도 | 시기 |
|------|--------|------|
| ai-worker-gpu Dockerfile (root, COPY 누락) | 저 | GPU 운영 시작 시 |
| schedule 앱 URL 미등록 | 저 | 기능 결정 시 |
| views 내 비즈니스 로직 → services 이동 | 중 | 도메인별 점진적 |
| docs_cursor/ → docs/ 통합 | 저 | 문서 정리 시 |

---

## 최종 구조

```
backend/
├── academy/                  # 헥사고날 코어 (통합 SSOT)
│   ├── domain/               # 순수 Python 엔티티
│   ├── application/          # ports, use_cases, services, video
│   ├── adapters/             # db, queue, cache, storage, video, ai, tools
│   └── framework/            # 워커 진입점
├── apps/
│   ├── api/                  # Django config + routing
│   ├── billing/              # 결제
│   ├── core/                 # Sealed 플랫폼 (views/ 9파일)
│   ├── domains/              # 22개 비즈니스 도메인 (표준화)
│   ├── support/              # messaging (분할), video
│   └── worker/               # 워커
├── libs/                     # phone_util, queue, r2_client, redis
├── docker/                   # Dockerfile + build scripts
└── requirements/             # pip 체인
```
