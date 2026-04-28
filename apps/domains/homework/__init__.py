"""
homework 도메인 — 숙제 정책·배정.

책임:
- HomeworkPolicy(세션 1:1 정책: cutline_percent, cutline_mode, clinic_enabled).
- HomeworkEnrollment(세션 단위 응시 자격).
- HomeworkAssignment(엔롤먼트 × 과제 배정).
- 점수 변경 viewset의 URL 라우팅 (HomeworkScoreViewSet 자체는 homework_results 소유).

⚠️ homework / homework_results 분리는 자의적이며, 향후 homework로 통합될 예정 (옵션 B).
신규 HomeworkScore 관련 코드는 homework_results 에 두고, 여기는 정책·등록·배정 + URL 라우팅만.

비책임 (다른 도메인 소유):
- Homework 정의 + HomeworkScore 점수 스냅샷: homework_results.
- 채점 로직: 없음 (숙제는 직접 점수 입력 — Submission 미경유 경로).
- 클리닉 합류: progress / clinic.

평가 5도메인 책임 분담은 backend/docs/00-SSOT/v1.1.1/HEXAGONAL-CUTOVER-POLICY.md §8 참조.
"""
