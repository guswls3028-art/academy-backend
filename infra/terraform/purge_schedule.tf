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

# ──────────────────────────────────────────────
# Orphan R2 storage cleanup — weekly
#
# Scheduling: 매주 일요일 04:00 KST = 토요일 19:00 UTC
# - purge_deleted_videos 와 경합 회피 위해 일별 배치와 분리된 시간대.
# - bucket 전체 스캔 비용 고려하여 주 1회.
# Trigger: EventBridge → SSM RunCommand → API EC2(docker exec)
#
# 대상:
#  - R2 raw/HLS/_tmp orphan (Video DB row 없음)
#  - 48시간 이상 경과한 PENDING Video 중 R2 raw 누락 행 → FAILED + soft-delete
# ──────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "cleanup_orphan_video_storage" {
  name                = "${var.naming_prefix}-cleanup-orphan-video-storage"
  description         = "Weekly cleanup of R2 video orphan files and stale PENDING rows, Sundays 04:00 KST"
  schedule_expression = "cron(0 19 ? * SAT *)" # 19:00 UTC Sat = 04:00 KST Sun
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "cleanup_orphan_video_storage" {
  rule      = aws_cloudwatch_event_rule.cleanup_orphan_video_storage.name
  target_id = "SsmRunCommandCleanupOrphanVideoStorage"
  arn       = "arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript"
  role_arn  = aws_iam_role.eventbridge_ssm_purge.arn

  run_command_targets {
    key    = "tag:Name"
    values = ["academy-v1-api"]
  }

  input = jsonencode({
    commands = [
      "docker exec academy-api python manage.py cleanup_orphan_video_storage --apply --min-age-hours=72"
    ]
  })
}

# ──────────────────────────────────────────────
# Auto-close overdue exams/homeworks — daily
#
# Scheduling: 매일 03:30 KST = 18:30 UTC
# - 어드민 사이드패널 useEffect 의존을 제거하기 위한 server-side enforcement.
# - "다음 차시 날짜가 도래하면 이전 차시의 OPEN 시험/과제는 자동 마감" 정책을
#   사이트 트래픽과 무관하게 보장.
# - purge_soft_deleted (03:15 KST) 이후 15분 간격으로 분리.
# Trigger: EventBridge → SSM RunCommand → API EC2(docker exec)
# ──────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "close_overdue_assessments" {
  name                = "${var.naming_prefix}-close-overdue-assessments"
  description         = "Daily auto-close of overdue exams/homeworks at 03:30 KST"
  schedule_expression = "cron(30 18 * * ? *)" # 18:30 UTC = 03:30 KST
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "close_overdue_assessments" {
  rule      = aws_cloudwatch_event_rule.close_overdue_assessments.name
  target_id = "SsmRunCommandCloseOverdueAssessments"
  arn       = "arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript"
  role_arn  = aws_iam_role.eventbridge_ssm_purge.arn

  run_command_targets {
    key    = "tag:Name"
    values = ["academy-v1-api"]
  }

  input = jsonencode({
    commands = [
      "docker exec academy-api python manage.py close_overdue_assessments"
    ]
  })
}
