# =============================================================================
# Module: Web App (ECS + Fargate + ALB + ECR) - Variables
# =============================================================================

variable "project_name" {
  description = "Name prefix for all web app resources"
  type        = string
}

variable "environment" {
  description = "Deployment environment name (for example dev, prod)"
  type        = string
}

variable "aws_region" {
  description = "AWS region for the web app resources"
  type        = string
}

# --- Networking (from the vpc module or existing IDs) ---
variable "vpc_id" {
  description = "VPC ID to deploy the ALB and Fargate service into"
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnets for the ALB"
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Private subnets for the Fargate tasks"
  type        = list(string)
}

variable "allowed_web_cidrs" {
  description = "CIDR blocks allowed to reach the ALB on HTTP. Leave empty to restrict to the VPC CIDR (private access only); set to your own network (or 0.0.0.0/0 at your own risk) to reach it externally."
  type        = list(string)
  default     = []
}

# --- Container / image ---
variable "context_dir" {
  description = "Path to the Docker build context (repo root)"
  type        = string
}

variable "image_tag" {
  description = "Image tag to build and deploy"
  type        = string
  default     = "latest"
}

variable "container_port" {
  description = "Port the container listens on"
  type        = number
  default     = 8080
}

variable "desired_count" {
  description = "Number of Fargate tasks to run"
  type        = number
  default     = 1
}

variable "task_cpu" {
  description = "Fargate task CPU units"
  type        = number
  default     = 512
}

variable "task_memory" {
  description = "Fargate task memory (MiB)"
  type        = number
  default     = 1024
}

# --- App configuration passed to the container ---
variable "artifacts_bucket" {
  description = "Name of the artifacts S3 bucket"
  type        = string
}

variable "artifacts_bucket_arn" {
  description = "ARN of the artifacts S3 bucket"
  type        = string
}

variable "results_bucket" {
  description = "Name of the results S3 bucket"
  type        = string
}

variable "results_bucket_arn" {
  description = "ARN of the results S3 bucket"
  type        = string
}

variable "state_table" {
  description = "Name of the DynamoDB run-state table"
  type        = string
}

variable "state_table_arn" {
  description = "ARN of the DynamoDB run-state table"
  type        = string
}

variable "transformer_project_name" {
  description = "Name of the transformer CodeBuild project the web app invokes"
  type        = string
}

variable "transformation_timeout_min" {
  description = "Per-run transformation timeout in minutes"
  type        = number
  default     = 30
}

variable "state_machine_arn" {
  description = "ARN of the Step Functions state machine the web app drives for closed-loop runs"
  type        = string
  default     = ""
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key ARN for ECR, CloudWatch logs, and DynamoDB access"
  type        = string
}

variable "alb_deletion_protection" {
  description = "Enable ALB deletion protection. Set false to allow `terraform destroy` to remove the ALB."
  type        = bool
  default     = true
}
