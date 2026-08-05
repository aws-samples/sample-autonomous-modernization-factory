variable "environment" {
  description = "Deployment environment name (for example dev, prod)"
  type        = string
}

variable "project_name" {
  description = "Name prefix for observability resources"
  type        = string
}

variable "state_machine_name" {
  type        = string
  description = "Step Functions state machine name for metrics"
}

variable "codebuild_project_name" {
  type        = string
  description = "CodeBuild project name for metrics"
}

variable "state_table_name" {
  type        = string
  description = "DynamoDB state table name for metrics"
}

variable "sns_alarm_topic_arn" {
  type        = string
  default     = ""
  description = "SNS topic for alarm notifications (empty = no notifications)"
}

variable "aws_region" {
  description = "AWS region used to build the dashboard URL"
  type        = string
  default     = "us-east-1"
}
