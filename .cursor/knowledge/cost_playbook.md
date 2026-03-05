# Cost Playbook

**Cost strategy:** balanced

**Instance rules:**

- Prefer ARM instances (t4g, c6g).
- **t4g.medium** for API, Messaging, AI workers.
- **c6g.large** for Video batch (heavy compute).
- Avoid: m5, c5, large instance fleets.

**Scaling:**

- Scale to zero not allowed (min workers = 1).
- max = 10 for ASG; maxvCpus = 10 for Batch.

**Cursor actions:**

- Detect idle ASG → recommend downsizing (respect min = 1).
- Detect oversized instances → recommend right-sizing to t4g.medium / c6g.large.
