# Docker 구조 정렬 완료 보고

**기준**: Big Bang 최종 단계 — 이상적 Docker 구조 물리 반영  
**검증 일시**: 체크리스트 검증 시점  
**SSOT**: 상수·포트·진입점 변경 없음

---

## 1. docker/Dockerfile.base (뿌리 강화) — ✅ 반영됨

| 요구사항 | 구현 상태 | 위치 |
|----------|-----------|------|
| builder: `pip install --upgrade pip`와 `common.txt` 설치 단일 RUN | ✅ | L25–27 `RUN pip install --upgrade pip && pip install --user --no-cache-dir -r requirements/common.txt` |
| runtime: appuser UID 1000 생성 | ✅ | L45–46 `groupadd --gid 1000 appuser` + `useradd --uid 1000 ...` |
| runtime: USER appuser | ✅ | L67 `USER appuser` |
| /app 경로 유지 | ✅ | WORKDIR /app, COPY 대상 모두 ./ 하위 |
| chown 처리 | ✅ | L60 `RUN chown -R appuser:appuser /app /home/appuser` |
| PATH에 .local/bin 포함 | ✅ | L52 `ENV PATH=/home/appuser/.local/bin:$PATH` |

---

## 2. docker/ai-worker-cpu/Dockerfile (통합) — ✅ 반영됨

| 요구사항 | 구현 상태 | 위치 |
|----------|-----------|------|
| FROM academy-base:latest 상속 | ✅ | L8–9 `ARG BASE_IMAGE=academy-base:latest` / `FROM ${BASE_IMAGE}` |
| FROM python:3.11-slim 폐기 | ✅ | 미사용 |
| src, apps, libs, manage.py 복사 제거 | ✅ | 베이스 상속만 사용, 해당 COPY 없음 |
| AI 전용 시스템 패키지(OCR 등) | ✅ | L15–21 apt: tesseract-ocr, tesseract-ocr-kor, ffmpeg, libgl1, libglib2.0-0 |
| scripts 복사만 추가 | ✅ | L32 `COPY scripts ./scripts` + chown (L33–35) |

---

## 3. 기타 Dockerfile 레이어 최적화 — ✅ 적용됨

| Dockerfile | apt-get | pip | 비고 |
|------------|---------|-----|------|
| video-worker | 1개 RUN (update + install + rm) | 1개 RUN | 논리적 단위로 이미 병합됨. USER root → apt → USER appuser → pip. |
| messaging-worker | 없음 | 1개 RUN | COPY + RUN pip 유지(캐시 효율). 베이스 상속으로 appuser. |
| api | 없음 | 1개 RUN | 동일. CMD·포트 8000 변경 없음. |

- **SSOT**: 상수·포트(8000)·진입점(CMD) 미변경 확인됨.

---

## 4. .dockerignore — ✅ AI 워커 빌드 방해 없음

| 항목 | 상태 | 비고 |
|------|------|------|
| scripts/ 제외 여부 | ✅ 제외 아님 | .dockerignore에 `scripts/` 없음. 주석에 "scripts/ 제거: ai-worker-cpu ... COPY scripts 필요" 명시. |
| ai-worker-cpu 빌드 시 컨텍스트 | ✅ scripts 포함 | `COPY scripts ./scripts` 시 scripts 디렉터리 포함됨. |

---

## 5. CMD·진입점·상수 검증

| 서비스 | CMD/진입점 | 변경 여부 |
|--------|------------|-----------|
| base | `python --version` | 없음 |
| api | gunicorn 0.0.0.0:8000, workers 등 | 없음 |
| video-worker | `python -m apps.worker.video_worker.sqs_main` | 없음 |
| messaging-worker | `python -m apps.worker.messaging_worker.sqs_main` | 없음 |
| ai-worker-cpu | `python -m apps.worker.ai_worker.sqs_main_cpu` | 없음 |

---

## 결론

**Docker 구조 정렬 완료.**

- Dockerfile.base: builder 단일 RUN, runtime non-root(appuser), /app·PATH·chown 반영.
- ai-worker-cpu: academy-base 상속, 중복 COPY 제거, AI 전용 apt·scripts만 추가.
- video/messaging/api: 레이어 이미 최소화, SSOT 유지.
- .dockerignore: scripts 제외 없음 → ai-worker-cpu 빌드 시 COPY scripts 정상 동작.

이상적 Docker 구조가 코드에 물리적으로 반영된 상태이며, 500 배포 및 10K 확장 시 위 구조를 그대로 사용하면 된다.
