# Academy SSOT v4 — Evidence 표 스키마

**역할:** Evidence 표 컬럼 고정. 리소스별 필드.

---

## 1. Batch Video

| 컬럼 | 설명 |
|------|------|
| batchVideoCeArn | academy-video-batch-ce-final ARN |
| batchVideoCeStatus | VALID |
| batchVideoCeState | ENABLED |
| videoQueueArn | academy-video-batch-queue ARN |
| videoQueueState | ENABLED |
| videoJobDefRevision | academy-video-batch-jobdef 최신 revision |
| videoJobDefVcpus | 2 |
| videoJobDefMemory | 3072 |

---

## 2. Batch Ops

| 컬럼 | 설명 |
|------|------|
| opsCeArn | academy-video-ops-ce ARN |
| opsCeStatus | VALID |
| opsCeState | ENABLED |
| opsQueueArn | academy-video-ops-queue ARN |
| opsQueueState | ENABLED |

---

## 3. EventBridge

| 컬럼 | 설명 |
|------|------|
| eventBridgeReconcileState | ENABLED/DISABLED |
| eventBridgeScanStuckState | ENABLED/DISABLED |

---

## 4. Netprobe

| 컬럼 | 설명 |
|------|------|
| netprobeJobId | 제출한 Job ID |
| netprobeStatus | SUCCEEDED |

---

## 5. ASG

| 컬럼 | 설명 |
|------|------|
| asgMessagingDesired, asgMessagingMin, asgMessagingMax, asgMessagingLtVersion | academy-messaging-worker-asg |
| asgAiDesired, asgAiMin, asgAiMax, asgAiLtVersion | academy-ai-worker-asg |

---

## 6. API / Build / SSM

| 컬럼 | 설명 |
|------|------|
| apiInstanceId | EIP에 연결된 InstanceId |
| apiBaseUrl | http://15.165.147.157:8000 |
| apiHealth | OK / status=xxx / unreachable |
| buildInstanceId | academy-build-arm64 InstanceId (있으면) |
| ssmWorkersEnvExists | yes/no |
| ssmShapeCheck | PASS/FAIL |

---

## 7. RDS / Redis

| 컬럼 | 설명 |
|------|------|
| rdsIdentifier | academy-db |
| rdsStatus | available |
| redisReplicationGroupId | academy-redis |
| redisStatus | available |
