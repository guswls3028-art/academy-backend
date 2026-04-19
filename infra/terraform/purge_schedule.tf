# ──────────────────────────────────────────────
# Data retention purge — soft-deleted students(30d) / videos(180d)
#
# Scheduling: 매일 03:15 KST = 18:15 UTC (purge-raw-videos 03:00 KST와 분리)
# Trigger: EventBridge → SSM RunCommand → API EC2(docker exec)
#
# 설계 이유:
#  - 개인정보 처리방침/이용약관에 명시된 보유기간(학생 30일, 영상 180일)을
#    실제로 이행하기 위해 자동 파기 배치가 필요.
#  - billing_schedule.tf와 동일한 SSM RunCommand 패턴 — 전용 Batch job을
#    두기엔 과함.
#  - purge_deleted_students: DB soft-deleted 30일 초과 건 완전 삭제.
#  - purge_deleted_videos: DB + R2 오브젝트 180일 초과 건 완전 삭제.
# ──────────────────────────────────────────────

resource "aws_iam_role" "eventbridge_ssm_purge" {
  name = "${var.naming_prefix}-eventbridge-ssm-purge-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy" "eventbridge_ssm_purge" {
  name = "${var.naming_prefix}-eventbridge-ssm-purge-policy"
  role = aws_iam_role.eventbridge_ssm_purge.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:SendCommand",
        ]
        Resource = [
          "arn:aws:ssm:${var.aws_region}:*:document/AWS-RunShellScript",
          "arn:aws:ec2:${var.aws_region}:*:instance/*",
        ]
      }
    ]
  })
}

resource "aws_cloudwatch_event_rule" "purge_soft_deleted" {
  name                = "${var.naming_prefix}-purge-soft-deleted"
  description         = "Daily purge of soft-deleted students(30d) and videos(180d) at 03:15 KST"
  schedule_expression = "cron(15 18 * * ? *)" # 18:15 UTC = 03:15 KST
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "purge_soft_deleted" {
  rule      = aws_cloudwatch_event_rule.purge_soft_deleted.name
  target_id = "SsmRunCommandPurgeSoftDeleted"
  arn       = "arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript"
  role_arn  = aws_iam_role.eventbridge_ssm_purge.arn

  run_command_targets {
    key    = "tag:Name"
    values = ["academy-v1-api"]
  }

  input = jsonencode({
    commands = [
      "docker exec academy-api python manage.py purge_deleted_students",
      "docker exec academy-api python manage.py purge_deleted_videos"
    ]
  })
}
