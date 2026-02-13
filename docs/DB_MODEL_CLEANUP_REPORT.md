# DB 모델 정리 (Redis 도입 후)

**결론**: 모든 필드 유지. 삭제 권장 없음.

- Video.leased_until, leased_by: VideoSQSQueue complete/fail 시 초기화. DB 일관성용.
- VideoPlaybackSession: last_seen, expires_at, violated_count, total_count — Write-Behind·fallback용.
- AIJobModel lease 필드: AI Worker SQS 로직과 연동. 보류.
