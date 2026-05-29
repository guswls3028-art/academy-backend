"""
OMR pipeline domain.

업로드 → AI 워커 분기 → 식별·매칭 → 답안 저장 → 채점 호출 → 상태 복구.

이 도메인의 책임은 OMR 한 장이 prod 입력에서 채점 완료까지 가는 단일 파이프라인을
원자적/멱등적으로 운영하는 것이다. submissions/ai/results/exams 도메인을 가로지르며
한 파일에 뭉쳐있던 책임을 단계별로 분리한다.

- contracts/  ← AI worker callback payload 정본 schema (pydantic) + 호환성 검증
- services/   ← 단계별 서비스 (receive/identify/match/persist/grade/recovery)
- api/        ← OMR 전용 HTTP entrypoint (검토·채택·재시도)
"""
