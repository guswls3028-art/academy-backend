"""
submissions 도메인 — 답안(answers) SSOT + 제출 상태머신.

책임:
- Submission(target_type=exam|homework, 상태머신: SUBMITTED→ANSWERS_READY→GRADING→DONE).
- SubmissionAnswer(문항별 raw answer 스냅샷).
- 제출 채널별 processor (OMR/ONLINE/HOMEWORK_IMAGE/AI_MATCH).
- transition.py = 상태 전이 SSOT.

비책임 (다른 도메인 소유):
- 채점 정확성·점수 계산: results.exam_grading_service.
  → dispatcher.py 가 grade_submission() 을 호출하는 경계.
- 시험 정의: exams.
- 결과 스냅샷·집계: results.

평가 5도메인 책임 분담은 backend/docs/00-SSOT/v1.1.1/HEXAGONAL-CUTOVER-POLICY.md §8 참조.
"""
