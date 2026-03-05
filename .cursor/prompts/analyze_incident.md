# Analyze Incident

Use this prompt to analyze a production incident.

**Steps:**

1. Read **topology for connections:** `.cursor/knowledge/infra_topology.yaml` (architecture_flow, services.connects_to/consumes) to map components and queues.
2. Gather: logs, metrics, infrastructure state (ASG, Batch, SQS, RDS, Redis).
3. Read 06_incident_analysis, knowledge/incident_playbook.md.
4. Common incidents: queue backlog, worker crash, batch stuck, DB connection exhaustion.
5. Pattern: SQS queue growing → check worker ASG health → recommend scaling or health check fix.
6. If drift: recommend script fix (scripts/v4), not manual changes.

**Output:** Root cause hypothesis, evidence, recommended actions (script or scaling within SSOT).
