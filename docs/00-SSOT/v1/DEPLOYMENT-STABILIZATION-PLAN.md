> **⚠️ SUPERSEDED — 배포 검증 절차는 `DEPLOY-VERIFICATION-SSOT.md` (V1.0.0)이 SSOT. 본 문서는 이력 참조용.**

# Deployment stabilization plan (DEPRECATED — verification moved to SSOT)

## Step 1 — Verified current infrastructure (facts)

### API ASG
- **Name:** academy-v1-api-asg
- **Launch template:** academy-v1-api-lt (Version 20)
- **Instance profile:** academy-ec2-role
- **Region:** ap-northeast-2
- **Running instance:** i-0b7ac7e2828a05c3f (t4g.medium, ap-northeast-2c, InService, Healthy)
- **Target group:** academy-v1-api-tg

### Docker on API instance (i-0b7ac7e2828a05c3f)
- **Container:** academy-api
- **Image:** 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api:latest
- **Ports:** 0.0.0.0:8000->8000/tcp
- **Status:** Up (healthy)

### Crontab on API instance (verified)
- **Present:** Yes. Entry runs every 2 minutes:
  - `*/2 * * * * flock -n /tmp/academy_deploy.lock bash -c 'cd /home/ec2-user/academy && git fetch origin main && ... && bash scripts/deploy_api_on_server.sh; fi' >> /home/ec2-user/auto_deploy.log 2>&1`

### State files on API instance
- `/home/ec2-user/auto_deploy.log` — EXISTS
- `/home/ec2-user/.academy-rapid-deploy-last` — EXISTS (deployed_at=2026-03-08T23:00:10+00:00)

### ECR
- **academy-api:latest** digest: sha256:30b7aaa3225b1ca40161658f8d5acf595a7ab4112506618847113d72d2892c21 (pushed 2026-03-09T08:02:06+09:00)

### Workers (unchanged)
- academy-v1-ai-worker-asg, academy-v1-messaging-worker-asg, Batch CE ASGs — no modifications.

---

## Step 2 — Non-stable deployment mechanisms (scripts)

| Script | Location | Capability |
|--------|----------|------------|
| deploy_api_on_server.sh | backend/scripts/deploy_api_on_server.sh | In-place container replacement: SSM→/opt/api.env, ECR pull, docker stop/rm/run academy-api |
| auto_deploy_cron_on.sh | backend/scripts/auto_deploy_cron_on.sh | Registers cron that runs deploy_api_on_server.sh every 2 min on main change |
| auto_deploy_cron_off.sh | backend/scripts/auto_deploy_cron_off.sh | Removes crontab entries containing deploy_api_on_server.sh |
| api-auto-deploy-remote.ps1 | backend/scripts/v1/api-auto-deploy-remote.ps1 | SSM RunCommand to API instances: On (cron on), Off (cron off), Deploy (run deploy_api_on_server.sh), Status |

No other git-based auto deploy watchers found (e.g. hot_deploy_watch.sh does not exist).

---

## Step 3 — Stabilization plan

### 1. Scripts to disable (exit immediately with guard)
- **api-auto-deploy-remote.ps1** — Add at top (after param block): guard message "Rapid deploy is disabled in production. Use CI/CD formal deploy." then exit 1.
- **auto_deploy_cron_on.sh** — Add at top: same guard, exit 1.
- **auto_deploy_cron_off.sh** — Add at top: same guard, exit 1.

### 2. Scripts that remain allowed
- **scripts/v1/deploy.ps1** — Formal deploy (Ensure-*, instance refresh). Not modified.
- **CI workflow** (v1-build-and-push-latest.yml) — ECR push + API ASG instance refresh. Not modified.
- **deploy_api_on_server.sh** — Add guard at top: same message, exit 1. Prevents in-place container replace even if run manually on server.

### 3. How cron auto deploy will be disabled
- **On API instance(s):** SSM RunCommand to run: remove crontab entries containing `deploy_api_on_server.sh` or `auto_deploy_cron_on.sh`. Confirm with `crontab -l` afterward.
- **In repo:** auto_deploy_cron_on.sh and api-auto-deploy-remote.ps1 will exit immediately, so cron cannot be re-enabled via these scripts.

### 4. Crontab entries currently exist
- **Yes.** One entry on i-0b7ac7e2828a05c3f (2-minute deploy_api_on_server.sh watcher).

### 5. Guarantee single deploy path
- **Only path:** GitHub Actions (push main) → build and push ECR → deploy-api-refresh job → `aws autoscaling start-instance-refresh` for academy-v1-api-asg. New/replaced instances get UserData that runs docker run academy-api once.
- **IAM 권한 적용 완료 (2026-03-11):** `academy-gha-ecr-build` 역할에 `autoscaling:StartInstanceRefresh`, `DescribeInstanceRefreshes`, `DescribeAutoScalingGroups` 권한 추가. CI deploy-api-refresh job 정상 동작 확인.
- **Removed:** Cron-based deploy, SSM rapid deploy (script exits), and re-enabling cron via scripts (guards).

---

## Step 4 — Execution order

1. **SSM:** Remove crontab entries on API instance i-0b7ac7e2828a05c3f; verify crontab clean.
2. **Repo:** Add guard (exit immediately with message) to:
   - backend/scripts/v1/api-auto-deploy-remote.ps1
   - backend/scripts/auto_deploy_cron_on.sh
   - backend/scripts/auto_deploy_cron_off.sh
   - backend/scripts/deploy_api_on_server.sh (defense in depth)
3. **No changes** to deploy.ps1, CI workflow, or worker infrastructure.

---

## Step 5 — Validation

- Run crontab -l on API instance again → no deploy-related entries.
- Run docker ps → academy-api still running.
- Confirm API ASG instance refresh still available (no code change to it).
- Confirm workers unchanged.
