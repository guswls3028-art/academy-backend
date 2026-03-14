# V1.1.0 — Next Version TODO

## Infrastructure (무중단 배포 전환)

### Required
- [ ] Zero-downtime deployment strategy (Rolling update or Blue-Green)
- [ ] Database migration strategy for zero-downtime
  - Backwards-compatible schema changes only
  - Two-phase migration: add → deploy → backfill → deploy → remove old
- [ ] Health check integration with ALB deployment pipeline
- [ ] Graceful shutdown handling (in-flight requests)
- [ ] Deployment rollback automation

### Recommended
- [ ] Canary deployment support (% traffic routing)
- [ ] Feature flags for gradual rollout
- [ ] Deployment notification webhook (Slack/Discord)
- [ ] Pre-deployment smoke test automation
- [ ] Automated migration verification pre-deploy

## Feature TODO

### Payment/Billing
- [ ] 결제 게이트웨이 연동 (PG: 토스페이먼츠/NHN KCP 등)
- [ ] 자동 결제 갱신 (월 정기 결제)
- [ ] 결제 내역 조회 UI
- [ ] 요금제 변경 (업그레이드/다운그레이드)
- [ ] 만료 N일 전 알림 발송 (이메일/SMS)
- [ ] 유예기간(grace) 자동 전환 로직

### Video Social
- [ ] 댓글 멘션(@) 자동완성
- [ ] 댓글 알림 연동 (notification system)
- [ ] 선생앱 영상 상세 페이지 리디자인 (댓글/좋아요 관리)
- [ ] 영상 좋아요 목록 조회

### General
- [ ] 학생 아바타 — 선생앱 학생 목록 등 아바타 표시 영역 확장
- [ ] AttendancePage (학생앱) — 백엔드 API 개발 필요
- [ ] Notification counts 백엔드 API (video/grade — 현재 하드코딩 0)
