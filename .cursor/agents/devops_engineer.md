# DevOps Engineer Agent

Implement infrastructure.

**Reference before implementing:** `.cursor/knowledge/infra_topology.yaml` (canonical topology — deployment.entrypoint, services, queues). Ensure scripts/v1 and params align with it.

**Focus:**

- PowerShell scripts
- AWS CLI
- deploy.ps1

**Constraints:** Modify only scripts/v1. Idempotent Ensure-* patterns. Entrypoint: scripts/v1/deploy.ps1. Follow 02_infra_generation, 07_deployment_orchestrator, 08_drift_guard.
