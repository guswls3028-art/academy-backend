# Video Worker — AWS Batch 전용.
# 엔트리: batch_entrypoint (SSM env → exec) → batch_main (1 job = 1 container).
# SQS/ASG 경로 없음. see README.md
