variable "environment" {
  description = "Deployment environment name (for example dev, prod)"
  type        = string
}

variable "project_name" {
  description = "Name prefix for storage resources"
  type        = string
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key ARN for DynamoDB encryption"
  type        = string
}
