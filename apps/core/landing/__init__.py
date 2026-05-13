"""apps/core/landing 패키지 — 점진 분리 (P1 audit 2026-05-14).

이전: apps/core/views_landing.py 1097 lines 단일 파일에 6 도메인 혼재
  (LandingPage CRUD / Consult / Testimonial / Manifest / Sitemap / HitReportToggle).

전략: facade 패턴 — 새 위치에서 view 정의 + 옛 views_landing.py 가 re-export.
import path 보존 (urls.py / matchup/views_hit_report.py 무수정).

현 단계: Manifest + Sitemap 분리. 후속 cycle 에서 view 점진 이동.
"""
