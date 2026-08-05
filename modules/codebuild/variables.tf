variable "environment" {
  description = "Deployment environment name (for example dev, prod)"
  type        = string
}

variable "project_name" {
  description = "Name prefix for CodeBuild resources"
  type        = string
}

variable "source_bucket" {
  description = "S3 bucket holding uploaded source and helper scripts"
  type        = string
}

variable "results_bucket" {
  description = "S3 bucket where build/transform/validation results are written"
  type        = string
}

variable "vpc_id" {
  description = "Optional VPC ID for CodeBuild network isolation (empty = no VPC)"
  type        = string
  default     = ""
}

variable "subnet_ids" {
  description = "Subnet IDs used when CodeBuild runs inside a VPC"
  type        = list(string)
  default     = []
}

variable "max_retry_attempts" {
  description = "Validate to remediate loops before human escalation"
  type        = number
  default     = 3
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key ARN for CodeBuild project encryption"
  type        = string
}

variable "transform_build_timeout" {
  description = "CodeBuild build timeout (minutes) for the transformer; align with the app transformation timeout."
  type        = number
  default     = 30
}

variable "bedrock_model_id" {
  description = "Bedrock model ID (or inference-profile ID) the remediator uses to reason about fixes."
  type        = string
  default     = "anthropic.claude-sonnet-4-20250514-v1:0"
}
