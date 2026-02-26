# AI 맥락 전달용 — 반드시 먼저 읽을 것

**다른 AI(GPT 등)에게 이 프로젝트를 넘길 때 이 파일을 첨부하거나 "이 문서 먼저 읽어라"라고 지시하라.**  
이 문서만 있으면 추측·가정을 줄이고, 아래 원칙과 확정 사실만 기준으로 답하도록 할 수 있다.

---

## 원칙 (추측 금지)

1. **답변은 이 리포지터리의 코드 또는 `docs/` 문서에만 근거한다.** 코드/문서에 없는 내용은 "추정입니다", "코드 확인 필요"라고 밝힌다.
2. **불명확하면 가정하지 말고 질문한다.** "일반적으로 …", "보통 …"으로 대체하지 않는다.
3. **다른 프로젝트·일반적인 AWS/백엔드 관례를 이 프로젝트에 그대로 적용하지 않는다.** 이 프로젝트는 아래 확정 사실과 코드·docs만 따른다.

---

## 확정 사실 (이 목록 외에는 추측하지 말 것)

| 구분 | 이 프로젝트에서 사용 | 사용하지 않음 / 주의 |
|------|----------------------|------------------------|
| **영상 원본·HLS 스토리지** | **Cloudflare R2** | AWS S3 아님. R2_* 설정, r2_paths, delete_object_r2_video 등. |
| **영상 인코딩 실행** | **AWS Batch** (컨테이너 1개 = 영상 1개) | SQS 기반 인코딩 워커 없음. batch_main.py, batch_submit.py. |
| **인코딩 작업 큐/트리거** | DB(VideoTranscodeJob) + Batch submit | SQS로 인코딩 job 전달하지 않음. |
| **R2 삭제** | SQS `academy-video-delete-r2` + Lambda | 인코딩 파이프라인과 별개. |
| **DB** | Django, PostgreSQL. VideoTranscodeJob, Video 모델. | Video에는 tenant_id 컬럼 없음. Session→Lecture→Tenant 경유. |
| **진입·검증** | `docs/video/batch/VIDEO_BATCH_SERVICE_LAUNCH_DESIGN_FOR_GPT.md`, `docs/video/batch/VIDEO_BATCH_DESIGN_VERIFICATION_REPORT.md` | 구현·경로는 반드시 코드/위 문서로 확인. |

---

## GPT 등에게 줄 지시 예시

- "첨부한 `docs/ai/AI_HANDOFF_CONTEXT.md`를 먼저 읽고, **추측 금지** 원칙과 **확정 사실**만 기준으로 답해줘. 코드에 없는 건 '추정'이라고 표시해줘."
- "이 프로젝트는 영상 스토리지가 R2고 인코딩은 Batch야. S3·SQS 인코딩 워커 얘기 하지 말고, `docs/`랑 코드만 보고 답해줘."

이 파일을 수정할 때는 실제 코드·설정과 맞추어 유지할 것.
