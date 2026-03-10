#!/usr/bin/env bash
# ==============================================================================
# API ↔ Video Batch 연결 상태 점검 (bash)
# ==============================================================================
# 사용: AWS_PROFILE=default bash scripts/v1/verify-video-batch-connection.sh
#       또는 export AWS_DEFAULT_REGION=ap-northeast-2 후 실행
# ==============================================================================

set -e
REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"

echo ""
echo "=== 1) SSM /academy/api/env — VIDEO_BATCH_* 값 ==="
RAW=$(aws ssm get-parameter --name "/academy/api/env" --region "$REGION" --with-decryption --query "Parameter.Value" --output text 2>&1) || true
if [[ -z "$RAW" ]]; then
  echo "  ERROR: SSM 조회 실패 또는 자격 증명 없음"
else
  if [[ "$RAW" =~ ^[A-Za-z0-9+/]+=*$ ]]; then
    RAW=$(echo "$RAW" | base64 -d 2>/dev/null || echo "$RAW")
  fi
  echo "$RAW" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    expected = {
        'VIDEO_BATCH_JOB_QUEUE': 'academy-v1-video-batch-queue',
        'VIDEO_BATCH_JOB_DEFINITION': 'academy-v1-video-batch-jobdef',
        'VIDEO_BATCH_JOB_QUEUE_LONG': 'academy-v1-video-batch-long-queue',
        'VIDEO_BATCH_JOB_DEFINITION_LONG': 'academy-v1-video-batch-long-jobdef',
    }
    for k, exp in expected.items():
        v = d.get(k, '')
        ok = (v == exp)
        status = 'OK' if ok else f'MISMATCH (expected: {exp})'
        print(f'  {k} = {v}')
        if not ok:
            print(f'    -> {status}')
except Exception as e:
    print('  ERROR:', e)
"
fi

echo ""
echo "=== 2) AWS Batch Job Queues ==="
aws batch describe-job-queues --region "$REGION" --query "jobQueues[*].jobQueueName" --output text 2>&1 | tr '\t' '\n' | while read -r q; do
  [[ -n "$q" ]] && echo "  $q"
done
echo "  (academy-v1-video-batch-queue 존재 여부 확인)"

echo ""
echo "=== 3) AWS Batch Job Definitions (ACTIVE) — video-batch ==="
aws batch describe-job-definitions --status ACTIVE --region "$REGION" --query "jobDefinitions[*].jobDefinitionName" --output text 2>&1 | tr '\t' '\n' | grep -E "video-batch|video-ops" || true

echo ""
echo "=== 4) AWS Batch Compute Environments ==="
aws batch describe-compute-environments --region "$REGION" --query "computeEnvironments[*].computeEnvironmentName" --output text 2>&1 | tr '\t' '\n' | while read -r c; do
  [[ -n "$c" ]] && echo "  $c"
done

echo ""
echo "=== 5) 최근 Batch Jobs (academy-v1-video-batch-queue) ==="
aws batch list-jobs --job-queue academy-v1-video-batch-queue --region "$REGION" --query "jobSummaryList[-5].{id:jobId,name:jobName,status:status}" --output table 2>&1 || echo "  (큐 없거나 권한 없음)"

echo ""
echo "=== 점검 완료 ==="
echo "연결 참조 문서: docs/00-SSOT/v1/reports/API-VIDEO-BATCH-REDIS-CONNECTION-REFERENCE.md"
