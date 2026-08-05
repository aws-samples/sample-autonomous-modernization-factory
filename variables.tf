# =============================================================================
# Variables - Modernization Factory
# =============================================================================

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "mod-factory"
}

variable "vpc_id" {
  description = "VPC ID for CodeBuild projects (optional, for private repo access)"
  type        = string
  default     = ""
}

variable "subnet_ids" {
  description = "Subnet IDs for CodeBuild VPC configuration"
  type        = list(string)
  default     = []
}

variable "bedrock_model_id" {
  description = "Bedrock model ID for agent reasoning (e.g., anthropic.claude-sonnet-4-20250514-v1:0)"
  type        = string
  default     = "anthropic.claude-sonnet-4-20250514-v1:0"
}

variable "remediation_model_id" {
  description = "Bedrock model used by the remediator. Newer Claude models require a cross-region inference-profile ID for on-demand InvokeModel (hence the 'us.' prefix)."
  type        = string
  default     = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
}

variable "max_retry_attempts" {
  description = "Maximum number of build-fix-retry loops before human escalation"
  type        = number
  default     = 3

  validation {
    condition     = var.max_retry_attempts >= 1 && var.max_retry_attempts <= 10
    error_message = "max_retry_attempts must be between 1 and 10."
  }
}

variable "complexity_threshold" {
  description = "Complexity score (0-100) above which apps route to human review instead of autonomous processing"
  type        = number
  default     = 70

  validation {
    condition     = var.complexity_threshold >= 0 && var.complexity_threshold <= 100
    error_message = "complexity_threshold must be between 0 and 100."
  }
}

variable "sns_escalation_topic_arn" {
  description = "SNS topic ARN for human-in-the-loop escalation notifications"
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Web app (modernization-web-app) - networking, transform, and deployment
# -----------------------------------------------------------------------------
variable "deploy_webapp" {
  description = "Whether to deploy the ECS/Fargate web app (requires Docker where terraform runs)"
  type        = bool
  default     = false
}

variable "image_tag" {
  description = "Container image tag for the API service"
  type        = string
  default     = "latest"
}

variable "allowed_web_cidrs" {
  description = "CIDR blocks allowed to reach the web app ALB on HTTP. Empty (default) restricts to the VPC CIDR; set to your own network to reach it externally."
  type        = list(string)
  default     = []
}

# Option A: create a new VPC via the vpc module.
variable "create_vpc" {
  description = "Create a new VPC (true) or reuse existing subnet IDs (false)"
  type        = bool
  default     = true
}

variable "vpc_cidr" {
  description = "CIDR block for the new VPC when create_vpc = true"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones for the new VPC subnets when create_vpc = true"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}

# Option B: reuse existing subnets (used when create_vpc = false).
variable "existing_vpc_id" {
  description = "Existing VPC ID for the web app when create_vpc = false"
  type        = string
  default     = ""
}

variable "public_subnet_ids" {
  description = "Existing public subnet IDs (ALB) when create_vpc = false"
  type        = list(string)
  default     = []
}

variable "private_subnet_ids" {
  description = "Existing private subnet IDs (Fargate) when create_vpc = false"
  type        = list(string)
  default     = []
}
