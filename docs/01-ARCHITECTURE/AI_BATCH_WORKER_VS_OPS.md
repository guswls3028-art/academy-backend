# Batch: 워커 vs Ops(Reconcile) 구분 — AI 참고용

ChatGPT 등 AI 채팅에서 Batch 관련 질문할 때 아래를 프롬프트에 붙여넣으면, **워커**와 **reconcile(ops)**를 섞지 않도록 할 수 있습니다.

---

## 구분 (혼동 금지)

| 구분 | 역할 | 큐 | Compute Environment | vCPU |
|------|------|-----|---------------------|------|
| **워커** | 영상 인코딩 실행 | `academy-video-batch-queue` | `academy-video-batch-ce-v2` (또는 v3) | min 0, **max 32** |
| **Ops / Reconcile** | reconcile_batch_video_jobs, scan_stuck, netprobe | `academy-video-ops-queue` | `academy-video-ops-ce` | min 0, **max 2** |

- **워커** = 비디오 인코딩 전용. JobDef `academy-video-batch-jobdef`. CE는 따로 띄움 (min 0, max 32).
- **Reconcile** = 5분마다 EventBridge로 `academy-video-ops-queue`에 제출되는 관리용 job. **워커가 아님.** CE는 ops 전용 (min 0, max 2).

**절대 하지 말 것:** reconcile/ops 큐·CE를 "워커"라고 부르거나, 워커 수정/진단을 ops 쪽에 적용하거나, ops CE min/max를 워커처럼 32로 맞추려 하지 말 것.

---

## 한 줄 요약 (프롬프트에 넣을 때)

```
Batch: 워커 = academy-video-batch-queue + academy-video-batch-ce-v2 (인코딩, max 32). Reconcile/Ops = academy-video-ops-queue + academy-video-ops-ce (관리 job, max 2). 둘 다 "워커"라고 부르지 말고 구분해서 써라.
```
