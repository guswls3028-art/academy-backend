# Security Groups — Minimal 5 SG design
# api-sg, worker-sg, batch-sg, redis-sg, rds-sg

locals {
  vpc_id = data.aws_vpc.main.id
}

resource "aws_security_group" "api" {
  name        = "${var.naming_prefix}-sg-app"
  description = "API instances - ALB health, app traffic"
  vpc_id      = local.vpc_id

  ingress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["172.30.0.0/16"]
    description = "ALB to API"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.naming_prefix}-sg-app"
  }
}

resource "aws_security_group" "worker" {
  name        = "${var.naming_prefix}-sg-worker"
  description = "Messaging + AI workers"
  vpc_id      = local.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.naming_prefix}-sg-worker"
  }
}

resource "aws_security_group" "batch" {
  name        = "${var.naming_prefix}-sg-batch"
  description = "Batch compute environments"
  vpc_id      = local.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.naming_prefix}-sg-batch"
  }
}

resource "aws_security_group" "redis" {
  name        = "${var.naming_prefix}-sg-redis"
  description = "Redis - allow from api, worker, batch"
  vpc_id      = local.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [
      aws_security_group.api.id,
      aws_security_group.worker.id,
      aws_security_group.batch.id
    ]
    description = "App and workers"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.naming_prefix}-sg-redis"
  }
}

resource "aws_security_group" "rds" {
  name        = "${var.naming_prefix}-sg-rds"
  description = "RDS - allow from api, worker, batch"
  vpc_id      = local.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [
      aws_security_group.api.id,
      aws_security_group.worker.id,
      aws_security_group.batch.id
    ]
    description = "App and workers"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.naming_prefix}-sg-rds"
  }
}
