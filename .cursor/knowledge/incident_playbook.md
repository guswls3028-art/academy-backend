# Incident Playbook

## Incident: SQS queue backlog

**Possible causes:**

- worker crash
- ASG capacity too low
- downstream API slow

**Actions:**

1. Check worker logs (CloudWatch / container logs).
2. Check ASG health (InService count, lifecycle).
3. Increase ASG max if needed (within 10); or fix health check / app errors.
4. Verify queue processing (SQS ApproximateNumberOfMessagesVisible, number in flight).

---

## Incident: Worker crash

**Actions:**

1. Check ASG instance lifecycle and health checks.
2. Check application logs and RDS/Redis connectivity.
3. Ensure minSize=1; replace failed instance via ASG.
4. If recurring: check 05_reliability_engine and scaling_playbook.

---

## Incident: Batch stuck

**Possible causes:**

- Batch CE insufficient capacity
- Job definition / image issue
- RDS or Redis unreachable from Batch

**Actions:**

1. Check Batch job status (RUNNING, FAILED, RUNNABLE).
2. Check Batch CE state and maxvCpus (limit 10).
3. Check job logs and SSM/SSOT for env/script drift.
4. Recommend script fix if drift; no manual console-only changes.

---

## Incident: DB connection exhaustion

**Actions:**

1. Check RDS connections and connection pool settings in app.
2. Check for connection leaks (long-running or stuck workers).
3. Scale workers within max=10; optimize pool size in app config (SSOT/env).
