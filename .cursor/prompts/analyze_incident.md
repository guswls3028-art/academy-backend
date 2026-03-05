# Analyze Incident

Use this prompt to analyze a production incident.

**Steps:**

1. Gather: logs, metrics, infrastructure state (ASG, Batch, SQS, RDS, Redis).
2. Read 06_incident_analysis, knowledge/incident_playbook.md.
3. Common incidents: queue backlog, worker crash, batch stuck, DB connection exhaustion.
4. Pattern: SQS queue growing → check worker ASG health → recommend scaling or health check fix.
5. If drift: recommend script fix (scripts/v4), not manual changes.

**Output:** Root cause hypothesis, evidence, recommended actions (script or scaling within SSOT).
