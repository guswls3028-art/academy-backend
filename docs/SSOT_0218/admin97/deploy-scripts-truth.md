# 배포 스크립트 — 진짜 쓰는 것만

실제 액세스 키는 문서에 넣지 말 것.

---

## 일상적으로 쓰는 건 3개뿐

| 스크립트 | 언제 |
|----------|------|
| **full_redeploy.ps1** | 풀배포 전부. 캐시 빌드 / 노캐시(-NoCache) / 워커만(-SkipBuild) 다 이거로. |
| **deploy_preflight.ps1** | 배포 전에 한 번 (계정·키·ECR·인스턴스 확인). 권장. |
| **redeploy_worker_asg.ps1** | 풀배포만 하면 워커가 잘못 뜨거나 문제 날 때. **풀배포 → 이거 → full_redeploy -SkipBuild** 순서로 돌리면 됨. |

## 긴급/가끔만

| 스크립트 | 언제 |
|----------|------|
| **remove_ec2_stop_from_worker_role.ps1** | 워커가 계속 껐다 켜질 때(self-stop 루프). 한 번만 실행. |
| **deploy_worker_asg.ps1** | 워커 LT/ASG 인프라를 처음 만들거나 완전히 다시 잡을 때. (보통은 redeploy_worker_asg.ps1이 이걸 호출함.) |

## 나머지 scripts/*.ps1

필요할 때만. 새 스크립트 추가하지 말고 이 문서·cheatsheet에서 찾아 쓰면 됨.

- set_aws_env.ps1 — .env.aws 로드
- upload_env_to_ssm.ps1, upload_google_vision_to_ssm.ps1 — env/OCR SSM 업로드
- launch_build_instance.ps1, restart_build_instance.ps1 — 빌드 인스턴스 수동 기동/재시작
- quick_api_restart.ps1, deploy_api_git_pull.ps1 — API만 빠르게
- add_current_ip_to_rds.ps1, check_rds_*.ps1 — RDS 접근
- check_instance_connect.ps1 — SSH/Instance Connect 원인 점검
- deploy_worker_autoscale.ps1 — 500 스케일 Lambda
- setup_worker_iam_and_ssm.ps1 — SSM/IAM 설정 (redeploy_worker_asg가 내부에서 호출)
- build_and_push_ecr.ps1 — 빌드 인스턴스 안 쓰고 로컬에서 ECR 푸시할 때
- 기타 check_*, setup_tenant_9999 등 — 이름 그대로 용도

상세 명령어·키 규칙·풀배포 절차: `docs_cursor/0218-deploy-setup-total.md` 참고.
