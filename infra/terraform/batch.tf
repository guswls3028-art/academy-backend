# Batch — Video CE + Ops CE, Queues, Job Definitions
# RDS, Redis, SQS, DynamoDB, ECR assumed to exist (DO NOT manage in Terraform)

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = var.aws_region
}

# Batch Service Role (use existing)
data "aws_iam_role" "batch_service" {
  name = "academy-batch-service-role"
}

data "aws_iam_role" "batch_ecs_instance" {
  name = "academy-batch-ecs-instance-role"
}

data "aws_iam_role" "batch_task_execution" {
  name = "academy-batch-ecs-task-execution-role"
}

data "aws_iam_role" "video_batch_job" {
  name = "academy-video-batch-job-role"
}

resource "aws_batch_compute_environment" "video" {
  compute_environment_name = "${var.naming_prefix}-video-batch-ce"
  type                    = "MANAGED"
  state                   = "ENABLED"

  compute_resources {
    type                = "EC2"
    allocation_strategy = "BEST_FIT_PROGRESSIVE"
    min_vcpus           = 0
    max_vcpus           = 40
    instance_types      = ["c6g.xlarge"]
    subnets             = var.private_subnet_ids
    security_group_ids  = [aws_security_group.batch.id]
    instance_role      = data.aws_iam_role.batch_ecs_instance.arn

    ec2_configuration {
      image_type = "ECS_AL2023"
    }
  }

  service_role = data.aws_iam_role.batch_service.arn
}

resource "aws_batch_compute_environment" "ops" {
  compute_environment_name = "${var.naming_prefix}-video-ops-ce"
  type                     = "MANAGED"
  state                    = "ENABLED"

  compute_resources {
    type                = "EC2"
    allocation_strategy = "BEST_FIT"
    min_vcpus           = 0
    max_vcpus           = 2
    instance_types      = ["m6g.medium"]
    subnets             = var.private_subnet_ids
    security_group_ids  = [aws_security_group.batch.id]
    instance_role      = data.aws_iam_role.batch_ecs_instance.arn

    ec2_configuration {
      image_type = "ECS_AL2023"
    }
  }

  service_role = data.aws_iam_role.batch_service.arn
}

resource "aws_batch_job_queue" "video" {
  name                 = "${var.naming_prefix}-video-batch-queue"
  state                = "ENABLED"
  priority             = 1
  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.video.arn
  }
}

resource "aws_batch_job_queue" "ops" {
  name                 = "${var.naming_prefix}-video-ops-queue"
  state                = "ENABLED"
  priority             = 1
  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.ops.arn
  }
}
