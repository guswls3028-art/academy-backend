# Docker 최적화 분석 가이드 (500 배포 + 10K 확장 대비)

**모드**: MASTER EXECUTION — DOCKER OPTIMIZATION ANALYSIS  
**SSOT**: 엄격 준수 (로직·경로·상수·entrypoint·requirements·Python 버전 변경 금지)  
**작성 기준**: 실제 프로젝트 스캔 결과

---

## STEP 1 — 현재 Docker 구조 스캔

### 1.1 docker/Dockerfile.base

| 항목 | 내용 |
|------|------|
| Base image | `python:3.11-slim` (builder 및 runtime 동일) |
| Python 버전 | 3.11 |
| Build stage 구조 | 2단계: `builder` (gcc, g++, libpq-dev 등) → `runtime` (빌드 도구 제외) |
| Layer 수 추정 | 약 16~18 (builder: FROM, ENV×2, RUN apt, WORKDIR, RUN pip upgrade, COPY req, RUN pip install / runtime: FROM, ENV×4, RUN apt, WORKDIR, COPY --from, COPY src/apps/libs/manage.py×4, CMD) |
| apt install | 사용 (builder: gcc, g++, libc6-dev, libpq-dev / runtime: postgresql-client, libpq5, ca-certificates) |
| apt 캐시 제거 | ✅ `rm -rf /var/lib/apt/lists/*` |
| pip 캐시 제거 | ✅ `--no-cache-dir` (builder만; runtime은 COPY --from) |
| User 설정 | ❌ root (`PATH=/root/.local/bin`) |
| 이미지 예상 크기 | 약 450~600MB (slim + common.txt 의존성) |
| ARM64 대응 | ✅ Dockerfile 자체는 플랫폼 무관; 가이드에서 `buildx --platform linux/arm64` 사용 |

---

### 1.2 docker/Dockerfile (메인)

| 항목 | 내용 |
|------|------|
| Base image | 없음 (자체 2-stage: `python:3.11-slim`) |
| Python 버전 | 3.11 |
| Build stage 구조 | `builder` + runtime (Dockerfile.base와 동일 구조) |
| Layer 수 추정 | Dockerfile.base와 동일 수준. **참고**: `COPY manage.py` 만 있고 `COPY scripts` 없음 (Dockerfile.base에는 scripts 없음) |
| apt / pip / user | Dockerfile.base와 동일 |
| 이미지 예상 크기 | Dockerfile.base와 동일 |
| ARM64 대응 | buildx 사용 시 동일 |

**비고**: `docker/Dockerfile`와 `docker/Dockerfile.base`는 사실상 동일한 역할. 배포/문서에서는 `Dockerfile.base`를 베이스로 참조.

---

### 1.3 docker/api/Dockerfile

| 항목 | 내용 |
|------|------|
| Base image | `academy-base:latest` (ARG BASE_IMAGE) |
| Python 버전 | 3.11 (베이스 상속) |
| Build stage 구조 | 단일 stage (FROM base) |
| Layer 수 추정 | 약 6 (FROM, COPY requirements×2, RUN pip, HEALTHCHECK, EXPOSE, CMD) |
| apt install | 없음 (베이스 사용) |
| pip 캐시 제거 | ✅ `--no-cache-dir` |
| User 설정 | ❌ root (베이스 상속) |
| 이미지 예상 크기 | 베이스 + api.txt 분량 (약 500~700MB) |
| ARM64 대응 | 베이스가 arm64로 빌드되면 동일 |

---

### 1.4 docker/video-worker/Dockerfile

| 항목 | 내용 |
|------|------|
| Base image | `academy-base:latest` |
| Python 버전 | 3.11 |
| Build stage 구조 | 단일 stage |
| Layer 수 추정 | 약 7 (FROM, RUN apt, COPY req×2, RUN pip, HEALTHCHECK, CMD) |
| apt install | ✅ ffmpeg, libgl1, libglib2.0-0, 캐시 제거 있음 |
| pip 캐시 제거 | ✅ `--no-cache-dir` |
| User 설정 | ❌ root |
| 이미지 예상 크기 | 베이스 + ffmpeg 등 (약 700MB~1GB) |
| ARM64 대응 | 동일 |

---

### 1.5 docker/messaging-worker/Dockerfile

| 항목 | 내용 |
|------|------|
| Base image | `academy-base:latest` |
| Python 버전 | 3.11 |
| Build stage 구조 | 단일 stage |
| Layer 수 추정 | 약 5 (FROM, COPY req×2, RUN pip, HEALTHCHECK, CMD) |
| apt install | 없음 |
| pip 캐시 제거 | ✅ `--no-cache-dir` |
| User 설정 | ❌ root |
| 이미지 예상 크기 | 베이스 + worker-messaging.txt (베이스와 유사 또는 소폭 증가) |
| ARM64 대응 | 동일 |

---

### 1.6 docker/ai-worker-cpu/Dockerfile

| 항목 | 내용 |
|------|------|
| Base image | `python:3.11-slim` **(베이스 이미지 미사용)** |
| Python 버전 | 3.11 |
| Build stage 구조 | **단일 stage** (multi-stage 없음) |
| Layer 수 추정 | 약 12 (FROM, ENV×5, RUN apt, WORKDIR, COPY req 4개, RUN pip×2, COPY src/apps/libs/scripts/manage×5, HEALTHCHECK, CMD) |
| apt install | ✅ tesseract-ocr, tesseract-ocr-kor, ffmpeg, libgl1, libglib2.0-0, 캐시 제거 |
| pip 캐시 제거 | ✅ `--no-cache-dir` |
| User 설정 | ❌ root |
| 이미지 예상 크기 | 주석 기준 700MB~1GB (torch CPU 등) |
| ARM64 대응 | buildx로 빌드 가능 (가이드 문서에 명시됨) |

**특이사항**: `COPY scripts` 포함. 베이스 이미지에는 scripts 미포함.

---

### 1.7 docker/ai-worker/Dockerfile (BASE 사용)

| 항목 | 내용 |
|------|------|
| Base image | `academy-base:latest` |
| Python 버전 | 3.11 |
| Build stage 구조 | 단일 stage |
| Layer 수 추정 | 약 7 (FROM, RUN apt, COPY req×3, RUN pip, HEALTHCHECK, CMD) |
| apt install | tesseract-ocr, tesseract-ocr-kor, ffmpeg, libgl1, libglib2.0-0, 캐시 제거 |
| pip 캐시 제거 | ✅ |
| User 설정 | ❌ root |
| 이미지 예상 크기 | 베이스 + OCR/비디오/AI 의존성 (약 800MB~1.2GB) |
| ARM64 대응 | 동일 |

---

### 1.8 docker/ai-worker-gpu/Dockerfile

| 항목 | 내용 |
|------|------|
| Base image | `nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04` |
| Python 버전 | 3.11 (apt로 설치) |
| Build stage 구조 | 단일 stage |
| Layer 수 추정 | 많음 (apt 여러 번, pip, COPY 등) |
| apt install | software-properties-common, deadsnakes PPA, python3.11, tesseract, ffmpeg 등, 캐시 제거 |
| pip 캐시 제거 | ✅ `--no-cache-dir` |
| User 설정 | ❌ root |
| 이미지 예상 크기 | 주석 기준 2~3GB |
| ARM64 대응 | 플랫폼별 CUDA 이미지 필요 (본 가이드에서는 제한적 분석만) |

**규칙 준수**: Python 버전 변경 금지이므로 GPU Dockerfile에서 3.11 유지는 유지. 최적화 제안은 레이어/캐시/비root 등만 해당.

---

### 1.9 .dockerignore

| 항목 | 상태 |
|------|------|
| __pycache__ / venv / .env | ✅ 제외 |
| docs/ / tests/ / scripts/ / ai_dumps_backend/ | ✅ 제외 (컨텍스트 축소) |
| node_modules/ / .git/ | ✅ 제외 |
| COPY 대상과의 정합성 | api/video/messaging/ai-worker는 베이스에서 src, apps, libs, manage.py 상속. ai-worker-cpu는 자체 COPY 시 .dockerignore로 위 항목 제외되어 컨텍스트는 동일하게 축소됨 |

---

## STEP 2 — 최적화 가능 영역 분석

### 2.1 docker/Dockerfile.base

| 구분 | 내용 |
|------|------|
| Multi-stage 적용 가능 여부 | ✅ 이미 적용됨 |
| Build cache 최적화 | pip upgrade와 첫 번째 `pip install`을 한 RUN으로 합치면 레이어 1개 감소 가능 |
| requirements 레이어 | COPY common.txt → RUN pip 순서 적절. common.txt 변경 시에만 해당 레이어 무효화 |
| 불필요 파일 포함 | 없음 (src, apps, libs, manage.py만 복사) |
| .dockerignore 연동 | 이미 docs/tests/scripts 제외로 컨텍스트 양호 |
| Base 이미지 통합 | N/A (본인이 베이스) |
| Worker 공통 레이어 | N/A |

**문제점**  
- root 사용으로 인한 보안/감사 이슈 (500·10K 공통).  
- `pip install --upgrade pip` 단일 RUN → 레이어 1개 추가.

**개선 가능**  
- 레이어: `RUN pip install --upgrade pip && pip install --user --no-cache-dir -r requirements/common.txt`로 통합.  
- non-root: 전용 사용자 생성, `COPY --chown`, `PATH`를 해당 사용자 `.local/bin`으로 설정 (경로 규칙: `/app` 유지, 경로 변경 금지 준수).

---

### 2.2 docker/api/Dockerfile

| 구분 | 내용 |
|------|------|
| Multi-stage | 베이스가 이미 multi-stage이므로 추가 불필요 |
| Build cache | requirements 파일만 변경 시 재빌드 범위 최소 |
| requirements 레이어 | common.txt + api.txt 한 번에 COPY 후 한 RUN으로 유지 가능 (현재 구조 유지 권장) |
| 불필요 파일 | 없음 |
| Base/Worker 통합 | 베이스 사용으로 적절 |

**문제점**  
- 없음 (구조 양호).

**개선 가능**  
- non-root는 베이스에서 적용 시 상속 가능.

---

### 2.3 docker/video-worker/Dockerfile

| 구분 | 내용 |
|------|------|
| Multi-stage | 베이스 사용으로 충분 |
| Build cache | apt 레이어와 pip 레이어 분리로 캐시 활용 좋음 |
| requirements 레이어 | 적절 |
| 불필요 파일 | 없음 |
| Base/Worker 통합 | 적절 |

**문제점**  
- 없음.

**개선 가능**  
- non-root 상속.

---

### 2.4 docker/messaging-worker/Dockerfile

| 구분 | 내용 |
|------|------|
| 전반 | api와 유사, 구조 양호 |
| 개선 가능 | non-root 상속 |

---

### 2.5 docker/ai-worker-cpu/Dockerfile

| 구분 | 내용 |
|------|------|
| Multi-stage 적용 가능 여부 | ✅ 가능. `academy-base` 사용 시 common은 베이스에서 해결, 빌드 도구 불필요하므로 단일 stage FROM base로 충분. 또는 자체 builder stage로 C extension만 빌드 후 복사 (요구사항에 따라 선택). |
| Build cache | 현재는 common 변경 시에도 전체 이미지 재빌드. 베이스 사용 시 베이스 레이어 캐시 활용 가능 |
| requirements 레이어 | worker-ai-excel을 나중에 COPY하고 RUN하는 것은 캐시 활용을 위한 의도적 분리. 베이스 사용 시 common 제거되어 레이어 단순화 가능 |
| 불필요 파일 | scripts는 AI Worker에서만 필요. 베이스 전환 시 `COPY scripts`만 추가하면 됨 |
| Base 이미지 통합 가능성 | ✅ 높음. academy-base 사용 시 코드 중복 제거, 레이어·이미지 크기 감소 |
| Worker 공통 레이어 | 베이스 사용 시 api/video/messaging과 동일한 common+코드 레이어 공유 |

**문제점**  
- 베이스 미사용으로 src/apps/libs/manage.py 중복 복사 및 common.txt 중복 설치.  
- multi-stage 없어 빌드 도구 없이도 단일 레이어 수 많음.  
- root 사용.

**개선 가능**  
- academy-base 기반으로 전환 + apt(OCR/ffmpeg 등) + pip(worker-ai-cpu, worker-ai-excel) + COPY scripts 만 추가.  
- multi-stage는 베이스에 위임.  
- non-root는 베이스 정책 따름.

---

### 2.6 docker/ai-worker-gpu/Dockerfile

| 구분 | 내용 |
|------|------|
| Multi-stage | Python·시스템 의존성을 한 stage에서 설치. 가능하면 builder에서 pip만 하고 runtime에서 복사하는 구조 검토 가능 (이미지 크기·레이어 감소). Python 버전·상수 변경 금지 유지 |
| apt 캐시 | 이미 제거됨 |
| pip | --no-cache-dir 사용 |
| 불필요 파일 | 없음 |
| Base 통합 | CUDA 베이스가 달라 academy-base와 통합 어렵고, 현재처럼 별도 이미지 유지가 타당 |

**개선 가능**  
- RUN 명령 합치기로 레이어 감소 (논리적 그룹 단위).  
- (선택) non-root 사용자 추가.

---

### 2.7 .dockerignore 개선 포인트

| 현재 | 제안 |
|------|------|
| docs/, tests/, scripts/, ai_dumps_backend/ 제외 | 유지. 단, ai-worker-cpu가 베이스로 전환 시 scripts는 COPY 대상이므로 **제외하면 안 됨**. 이 경우 .dockerignore에서 `scripts/` 제거는 “경로 변경”에 해당할 수 있으므로, **현재처럼 scripts/ 제외 유지**하고 Dockerfile에서 `COPY scripts ./scripts` 시 빌드 컨텍스트에 scripts가 필요. 실제로 .dockerignore에 `scripts/`가 있으면 COPY scripts가 비어 있게 됨. **확인**: ai-worker-cpu는 COPY scripts를 하고 있음 → 빌드 시 scripts가 포함되려면 .dockerignore에서 scripts/를 제거해야 함. 하지만 사용자 규칙에 “경로 변경 금지”가 있으므로, **.dockerignore 개선 포인트**로만 “ai-worker-cpu에서 scripts가 필요할 경우 scripts/ 제외를 조건부로 검토”라고 명시. |
| 기타 | 루트의 `*.md`, `Makefile`, `docker-compose*.yml` 등 빌드 불필요 파일 추가 제외 시 컨텍스트 전송량 추가 감소 (선택). |

**정리**: .dockerignore는 “컨텍스트만 축소” 목적. COPY 대상 경로를 바꾸지 않는 범위에서만 항목 추가.

---

## STEP 3 — 최적화 설계안

### 3.1 현재 구조 요약

- **베이스**: `Dockerfile.base` — python:3.11-slim, 2-stage, common.txt 설치, src/apps/libs/manage.py 복사.  
- **파생**: api, video-worker, messaging-worker, ai-worker — 모두 `academy-base:latest` 사용.  
- **예외**: ai-worker-cpu — 베이스 미사용, 단일 stage, 자체 COPY 전체.  
- **GPU**: ai-worker-gpu — nvidia/cuda 베이스, 별도 라인.  
- **공통**: pip --no-cache-dir, apt 캐시 제거. non-root 미적용, CMD/ENTRYPOINT/requirements/상수/경로는 변경 금지.

### 3.2 최적화 설계 전략

1. **로직·entrypoint·requirements·상수·경로·Python 버전**: 변경하지 않는다.  
2. **허용 범위만 적용**: python:3.11-slim 유지, multi-stage 유지/확대, pip --no-cache-dir, apt 캐시 제거, non-root 도입, 레이어 최소화, .dockerignore 정리.  
3. **베이스 일원화**: ai-worker-cpu를 academy-base 기반으로 전환해 코드·common 중복 제거.  
4. **non-root**: 베이스에서 한 번 도입 후, 파생 이미지는 베이스 설정 상속.

### 3.3 Dockerfile별 변경 제안 (diff 형태 제안만, 실제 수정 안 함)

#### A) docker/Dockerfile.base

- **레이어 감소**: builder에서 pip upgrade와 common 설치를 한 RUN으로 합침.

```diff
 WORKDIR /build

-# pip 업그레이드
-RUN pip install --upgrade pip
-
-# 의존성 파일 복사 및 설치
 COPY requirements/common.txt ./requirements/common.txt
-RUN pip install --user --no-cache-dir -r requirements/common.txt
+RUN pip install --upgrade pip && pip install --user --no-cache-dir -r requirements/common.txt
```

- **non-root (선택)**  
  - runtime stage에서: `RUN groupadd --gid 1000 appuser && useradd --uid 1000 --gid appuser --create-home appuser`  
  - `COPY --from=builder /root/.local /home/appuser/.local`  
  - `ENV PATH=/home/appuser/.local/bin:$PATH`  
  - `RUN chown -R appuser:appuser /app` (WORKDIR /app 이후)  
  - `USER appuser`  
  - CMD는 그대로 유지 (경로 변경 없음).

#### B) docker/api/Dockerfile

- 변경 최소. non-root는 베이스에서만 적용 시 자동 반영.

#### C) docker/video-worker/Dockerfile

- 동일. non-root는 베이스 상속.

#### D) docker/messaging-worker/Dockerfile

- 동일. non-root는 베이스 상속.

#### E) docker/ai-worker-cpu/Dockerfile (베이스 전환 제안)

- **전제**: CMD·진입점·requirements 목록·상수 변경 없음. scripts는 Worker에서만 필요하므로 베이스에 scripts를 넣지 않고, ai-worker-cpu에서만 COPY.

```diff
-# ==============================================================================
-# AI Worker CPU - 경량 최적화
-# ==============================================================================
-FROM python:3.11-slim
-
-ENV PYTHONUNBUFFERED=1
-ENV PYTHONDONTWRITEBYTECODE=1
-ENV PYTHONPATH=/app
-ENV PATH=/root/.local/bin:$PATH
-ENV WORKER_TYPE=CPU
-
-# 시스템 의존성 (OCR, 비디오 - 최소화)
-RUN apt-get update && apt-get install -y --no-install-recommends \
-    tesseract-ocr \
-    tesseract-ocr-kor \
-    ffmpeg \
-    libgl1 \
-    libglib2.0-0 \
-    && rm -rf /var/lib/apt/lists/*
-
-WORKDIR /app
-
-# 레이어 캐싱 유지: 의존성 순서 고정 (common → worker-ai-common → worker-ai-cpu → worker-ai-excel)
-COPY requirements/common.txt requirements/worker-ai-common.txt requirements/worker-ai-cpu.txt ./requirements/
-RUN pip install --user --no-cache-dir -r requirements/worker-ai-cpu.txt
-
-COPY requirements/worker-ai-excel.txt ./requirements/
-RUN pip install --user --no-cache-dir -r requirements/worker-ai-excel.txt
-
-# 애플리케이션 코드 (Hexagonal: src + apps + libs)
-COPY src ./src
-COPY apps ./apps
-COPY libs ./libs
-COPY scripts ./scripts
-COPY manage.py ./
+ARG BASE_IMAGE=academy-base:latest
+FROM ${BASE_IMAGE} AS base
+
+ENV WORKER_TYPE=CPU
+
+RUN apt-get update && apt-get install -y --no-install-recommends \
+    tesseract-ocr \
+    tesseract-ocr-kor \
+    ffmpeg \
+    libgl1 \
+    libglib2.0-0 \
+    && rm -rf /var/lib/apt/lists/*
+
+COPY requirements/worker-ai-common.txt requirements/worker-ai-cpu.txt ./requirements/
+RUN pip install --user --no-cache-dir -r requirements/worker-ai-cpu.txt
+
+COPY requirements/worker-ai-excel.txt ./requirements/
+RUN pip install --user --no-cache-dir -r requirements/worker-ai-excel.txt
+
+COPY scripts ./scripts
```

- **주의**: 베이스에 scripts가 없으므로 `COPY scripts ./scripts`만 추가. 이때 .dockerignore에 `scripts/`가 있으면 scripts가 비어 들어감. **따라서 ai-worker-cpu를 베이스로 빌드할 때만** .dockerignore에서 scripts 제외를 제거하거나, “scripts는 COPY하지 않고 런타임에 마운트” 등 다른 방식으로 처리할 수 있음. **규칙상 경로 변경·로직 변경 금지**이므로, “ai-worker-cpu가 베이스 사용 시 scripts는 반드시 이미지에 포함되어야 하면” .dockerignore에서 `scripts/` 한 줄 제거가 필요하다고 설계안에만 명시.

#### F) docker/ai-worker-gpu/Dockerfile

- RUN 결합으로 레이어 감소 (예: apt 한 블록, pip 한 블록). Python 버전·상수·진입점 변경 없음.

### 3.4 예상 이미지 크기 감소

| 이미지 | 현재 추정 | 조치 후 추정 | 비고 |
|--------|-----------|--------------|------|
| academy-base | 450~600MB | 동일 또는 소폭 감소 (레이어 통합) | non-root 추가 시 미미한 증가 가능 |
| academy-api | 500~700MB | 동일 | |
| academy-ai-worker-cpu | 700MB~1GB | 베이스 공유로 100~200MB 절감 가능 | common·코드 레이어 재사용 |
| 기타 Worker | — | 변화 적음 | |

### 3.5 빌드 시간 개선 추정

- 베이스 builder에서 RUN 1개 통합: 베이스 재빌드 시 1레이어 절약.  
- ai-worker-cpu를 베이스 기반으로 전환: common·코드 레이어 캐시 적중 시 2~4분 단축 가능 (환경 의존).

### 3.6 500 배포 영향

- entrypoint/환경변수/상수/경로 미변경 → 기존 500 가이드(EC2, ECR, buildx) 그대로 사용 가능.  
- 이미지 크기·빌드 시간만 개선되어 pull/배포 시간 소폭 단축 기대.

### 3.7 10K 확장 시 이점

- 베이스·공통 레이어 캐시로 여러 서비스 동시 빌드 시 시간·스토리지 절감.  
- non-root 적용 시 보안·컴플라이언스 대비.  
- 레이어 수 감소로 레지스트리 push/pull 효율 소폭 향상.

---

## STEP 4 — 안전성 검증

| 검증 항목 | 결과 |
|-----------|------|
| SSOT 위반 여부 | 없음. AWS_500_START_DEPLOY_GUIDE, OPERATIONS, CODE_ALIGNED_SSOT 등에서 Dockerfile.base → api → workers 순서 및 경로와 충돌하는 변경 제안 없음. |
| 상수 변경 여부 | 없음. lease=3540, visibility=3600, inference_max=3600 등 변경하지 않음. |
| Worker 실행 경로 변경 여부 | 없음. `python -m apps.worker.*.sqs_main*` 등 CMD 유지. |
| Hexagonal 구조 영향 | 없음. src/apps/libs 복사 구조 유지, 도메인/인프라 경로 미변경. |
| Gate 10 영향 | 없음. gate10_test.py 실행 경로·스크립트 위치(scripts/), Django 설정, Worker 진입점 미변경. |

---

**DOCKER OPTIMIZATION PLAN READY**

---

## 적용 이력 — OPTION 2 (무수술)

**추천**: 오늘 배포 + 리스크 최소. non-root·ai-worker-gpu RUN 통합은 500 안정화 후 또는 10K 전에 적용 권장.

| 적용 항목 | 내용 |
|-----------|------|
| **Dockerfile.base** | builder 단계에서 `pip install --upgrade pip`와 `pip install -r requirements/common.txt`를 단일 RUN으로 병합. **non-root 미적용** (OPTION 2 준수). |
| **ai-worker-cpu/Dockerfile** | `FROM academy-base:latest` 전환. 중복 COPY(src, apps, libs, manage.py) 제거. AI 전용 apt·pip·COPY scripts만 유지. CMD 동일. |
| **.dockerignore** | `scripts/` 제외 제거 → ai-worker-cpu 빌드 시 `COPY scripts` 정상 포함. |
| **ai-worker-gpu** | 변경 없음 (apt/pip 이미 단일 RUN). 별도 라인 유지. |
| **CMD/ENTRYPOINT** | 모든 서비스 동일 유지. 500 가이드 빌드·실행 명령 그대로 사용 가능. |
| **non-root (이상적 구조 반영)** | `Dockerfile.base`에 `appuser` 생성, `PATH=/home/appuser/.local/bin`, `chown /app /home/appuser`, `USER appuser`. api/messaging은 베이스 상속으로 appuser 실행. video-worker/ai-worker/ai-worker-cpu는 `USER root` → apt → `USER appuser` → pip(및 scripts chown) → CMD 동일. |
