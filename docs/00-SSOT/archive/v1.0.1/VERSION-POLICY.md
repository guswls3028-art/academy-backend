# Version Policy

**Effective:** V1.0.1 (2026-03-11)

---

## Versioning Scheme

`V1.{MINOR}.{PATCH}`

- **V1** — Major version (V1 platform, locked architecture)
- **MINOR** — Feature additions, significant changes
- **PATCH** — Bug fixes, quality improvements, polish

---

## Version History

| Version | Date | Type | Description |
|---------|------|------|-------------|
| V1.0.0 | 2026-03-10 | Initial | V1 platform launch |
| V1.0.1 | 2026-03-11 | Quality | Alert→toast, learning tabs split, UX polish |

---

## Release Process

### 1. Pre-Release
1. Run full build (`npx vite build`)
2. Verify no TypeScript errors in changed files
3. Run audit checks (alert count, console.log, TODO text)

### 2. Deploy
- **Frontend:** `git push origin main` (Cloudflare Pages auto-deploy)
- **Backend:** GitHub Actions CI/CD (OIDC build → ECR → ASG refresh)
- **Backend manual:** `pwsh scripts/v1/deploy.ps1 -AwsProfile default`

### 3. Post-Deploy Verification
Follow `DEPLOY-VERIFICATION-SSOT.md` (V1.0.0 Locked):
1. CI/CD build status
2. `/healthz` (200) + `/health` (200)
3. ASG instances (Healthy, InService)
4. SQS queue depth + DLQ
5. Drift check

### 4. Documentation
- Create `docs/00-SSOT/v{VERSION}/` folder
- Required files: RELEASE-NOTES.md, DEPLOYMENT-STATE.md
- Optional: AUDIT-REPORT.md, FEATURE-MAP.md, ARCHITECTURE.md

---

## Next Version

**V1.0.2** — Next deployment will be documented under `docs/00-SSOT/v1.0.2/`
