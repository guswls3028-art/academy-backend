# Evidence / Audit
**Generated:** 2026-07-31T09:11:36.9423294+09:00

- **batchVideoCeArn:** arn:aws:batch:ap-northeast-2:809466760795:compute-environment/academy-v1-video-batch-ce-200gb
- **batchVideoCeStatus:** VALID
- **batchVideoCeState:** ENABLED
- **opsCeArn:** arn:aws:batch:ap-northeast-2:809466760795:compute-environment/academy-v1-video-ops-ce
- **opsCeStatus:** VALID
- **opsCeState:** ENABLED
- **videoQueueArn:** arn:aws:batch:ap-northeast-2:809466760795:job-queue/academy-v1-video-batch-queue
- **videoQueueState:** ENABLED
- **opsQueueArn:** arn:aws:batch:ap-northeast-2:809466760795:job-queue/academy-v1-video-ops-queue
- **opsQueueState:** ENABLED
- **videoJobDefRevision:** 340
- **videoJobDefVcpus:** 2
- **videoJobDefMemory:** 4096
- **eventBridgeReconcileState:** ENABLED
- **eventBridgeScanStuckState:** ENABLED
- **netprobeJobId:**
- **netprobeStatus:** skipped
- **asgMessagingDesired:** 1
- **asgMessagingMin:** 1
- **asgMessagingMax:** 3
- **asgMessagingLtVersion:** $Latest
- **asgAiDesired:** 0
- **asgAiMin:** 0
- **asgAiMax:** 5
- **asgAiLtVersion:** $Latest
- **asgToolsDesired:** 0
- **asgToolsMin:** 0
- **asgToolsMax:** 2
- **asgToolsLtVersion:** $Latest
- **apiInstanceId:** n/a (EIP not used)
- **apiAsgDesired:** 1
- **apiAsgMin:** 1
- **apiAsgMax:** 3
- **apiAsgLtVersion:** $Latest
- **apiBaseUrl:** http://academy-v1-api-alb-1244943981.ap-northeast-2.elb.amazonaws.com
- **apiHealthUrl:** https://api.hakwonplus.com/health
- **apiHealth:** OK
- **ssmWorkersEnvExists:** yes
- **ssmShapeCheck:** PASS
- **sqsScalingEnforced:** yes

**Verification Run ID:** 424872f450804a09afa927db3f5749a8
