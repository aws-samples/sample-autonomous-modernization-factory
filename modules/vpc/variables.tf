# =============================================================================
# Module: VPC - Variables
# =============================================================================

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones to spread subnets across. Must be in an AWS Transform custom supported region."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]

  validation {
    condition     = length(var.availability_zones) >= 2
    error_message = "Provide at least two availability zones for high availability (ALB requires >= 2 subnets)."
  }
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (one per AZ; used for the ALB). Length must match availability_zones."
  type        = list(string)
  default     = ["10.0.0.0/24", "10.0.1.0/24"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (one per AZ; used for Fargate tasks). Length must match availability_zones."
  type        = list(string)
  default     = ["10.0.10.0/24", "10.0.11.0/24"]
}

variable "enable_nat_gateway" {
  description = "Whether to create NAT gateways so private subnets have outbound internet egress (required for Fargate to pull the ECR image and reach AWS Transform)."
  type        = bool
  default     = true
}

variable "single_nat_gateway" {
  description = "If true, create a single shared NAT gateway (cost saving, lower availability). If false, one NAT gateway per AZ."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Additional tags to apply to all VPC resources"
  type        = map(string)
  default     = {}
}
