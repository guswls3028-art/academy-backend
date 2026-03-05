# Scaling Playbook

**Scaling signals:**

- SQS queue depth
- CPU utilization
- job backlog

**Limits:**

- max concurrency = 10
- Worker ASG: min = 1, max = 10
- Batch CE: maxvCpus = 10

**Actions:**

- Queue backlog high → check worker ASG health → increase capacity (within max) or fix health checks
- Batch stuck → check Batch CE/job definition and RDS/Redis connectivity
- Scale only via scripts/v4 and params.yaml; Ensure-* idempotent
