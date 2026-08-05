variable "environment" {
  description = "Deployment environment name (for example dev, prod)"
  type        = string
}

variable "project_name" {
  description = "Name prefix for orchestration resources"
  type        = string
}

variable "codebuild_project" {
  description = "Validator CodeBuild project name (build/test gate)"
  type        = string
}

variable "transformer_project" {
  description = "Transformer CodeBuild project name (runs the AWS Transform atx CLI)"
  type        = string
}

variable "remediator_project" {
  description = "Remediator CodeBuild project name (AI auto-fix on failed validation)"
  type        = string
}

variable "artifacts_bucket" {
  description = "S3 bucket holding uploaded source artifacts"
  type        = string
}

variable "results_bucket" {
  description = "S3 bucket where run results are written"
  type        = string
}

variable "max_retry_attempts" {
  description = "Validate to remediate loops before human escalation"
  type        = number
  default     = 3
}

variable "complexity_threshold" {
  description = "Complexity score above which runs route to human review"
  type        = number
  default     = 70
}

variable "sns_escalation_topic_arn" {
  description = "Optional SNS topic ARN for human-review escalation (empty = no notifications)"
  type        = string
  default     = ""
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key ARN for the state machine log group"
  type        = string
}
