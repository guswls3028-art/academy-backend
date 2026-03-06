variable "aws_region" {
  default = "ap-northeast-2"
}

variable "vpc_id" {
  description = "Existing VPC ID (academy-v1-vpc)"
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnet IDs"
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Private subnet IDs"
  type        = list(string)
}

variable "environment" {
  default = "prod"
}

variable "naming_prefix" {
  default = "academy-v1"
}
