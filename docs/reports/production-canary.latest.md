# Production Canary

**Generated:** 2026-06-24T22:39:13.3544213+09:00
**Mode:** PostDeploy
**Verdict:** PASS

| Stage | Name | Status | Detail |
|-------|------|--------|--------|
| HTTP | api_healthz | PASS | HTTP 200 |
| HTTP | api_http_redirects_to_https | PASS | HTTP 301 location=https://api.hakwonplus.com/healthz |
| HTTP | api_health | PASS | HTTP 200 |
| HTTP | api_readyz | PASS | HTTP 200 |
| HTTP | front_root | PASS | HTTP 200 |
| HTTP | front_promo | PASS | HTTP 200 |
| HTTP | api_program_tenant_healthy | PASS | HTTP 200 |
| HTTP | api_invalid_login_no_5xx | PASS | HTTP 400 |
| AWS | aws_identity | PASS | account=809466760795 |
| AWS | api_asg | PASS | 1 healthy / min=1 desired=1 max=3 |
| AWS | messaging_asg | PASS | 0 healthy / min=0 desired=0 max=3 |
| AWS | ai_asg | PASS | 0 healthy / min=0 desired=0 max=5 |
| AWS | tools_asg | PASS | 0 healthy / min=0 desired=0 max=2 |
| AWS | alb_target_health | PASS | 1/1 healthy |
| AWS | rds_status | PASS | available |
| AWS | redis_status | PASS | available |
| AWS | messaging_queue | PASS | visible=0 in_flight=0 |
| AWS | messaging_dlq | PASS | visible=0 in_flight=0 threshold=5 |
| AWS | ai_queue | PASS | visible=0 in_flight=0 |
| AWS | ai_dlq | PASS | visible=0 in_flight=0 threshold=5 |
| AWS | tools_queue | PASS | visible=0 in_flight=0 |
| AWS | tools_dlq | PASS | visible=0 in_flight=0 threshold=5 |
| AWS | cloudwatch_service_alarms | PASS | no service alarms |
| AWS | video_batch_queue | PASS | ENABLED/VALID |
| AWS | video_batch_ce | PASS | ENABLED/VALID |
| AWS | video_ops_queue | PASS | ENABLED/VALID |
| AWS | video_ops_ce | PASS | ENABLED/VALID |
| REMOTE | django_check_deploy | PASS | instance=i-08ebf442a47a3ce23 status=Success rc=0 |
| REMOTE | django_migrations_applied | PASS | instance=i-08ebf442a47a3ce23 status=Success rc=0 |
| REMOTE | django_production_canary | PASS | instance=i-08ebf442a47a3ce23 status=Success rc=0 |
