# =============================================================================
# AI-Powered Modernization Factory - Root Module
# Architecture: ATX Agent → Kiro Agent → CodeBuild Agent (closed loop via MCP)
# Orchestrated by Amazon Bedrock AgentCore
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Remote state backend. Create the bucket + lock table first with
  # `make bootstrap`, then uncomment this block and fill in the values it
  # outputs (see README "Quick Start - 1. Create the state backend").
  # S3 backends cannot use variables, so the values must be literals.
  # backend "s3" {
  #   bucket         = "<YOUR_STATE_BUCKET>"   # e.g. modernization-factory-tfstate-123456789012
  #   key            = "factory/terraform.tfstate"
  #   region         = "<YOUR_REGION>"         # e.g. us-east-1
  #   dynamodb_table = "modernization-factory-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "modernization-factory"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# Module: Storage - S3 buckets for artifacts, DynamoDB for state tracking
# -----------------------------------------------------------------------------
module "storage" {
  source = "./modules/storage"

  environment  = var.environment
  project_name = var.project_name
  kms_key_arn  = aws_kms_key.main.arn
}

# -----------------------------------------------------------------------------
# Module: CodeBuild - Build/test validation projects
# -----------------------------------------------------------------------------
module "codebuild" {
  source = "./modules/codebuild"

  environment        = var.environment
  project_name       = var.project_name
  source_bucket      = module.storage.artifacts_bucket_name
  results_bucket     = module.storage.results_bucket_name
  vpc_id             = var.vpc_id
  subnet_ids         = var.subnet_ids
  max_retry_attempts = var.max_retry_attempts
  bedrock_model_id   = var.remediation_model_id
  kms_key_arn        = aws_kms_key.main.arn
}

# -----------------------------------------------------------------------------
# Module: Orchestration - Step Functions state machine for the closed loop
# -----------------------------------------------------------------------------
module "orchestration" {
  source = "./modules/orchestration"

  environment              = var.environment
  project_name             = var.project_name
  codebuild_project        = module.codebuild.project_name
  transformer_project      = module.codebuild.transformer_project_name
  remediator_project       = module.codebuild.remediator_project_name
  artifacts_bucket         = module.storage.artifacts_bucket_name
  results_bucket           = module.storage.results_bucket_name
  max_retry_attempts       = var.max_retry_attempts
  complexity_threshold     = var.complexity_threshold
  sns_escalation_topic_arn = var.sns_escalation_topic_arn
  kms_key_arn              = aws_kms_key.main.arn
}

# -----------------------------------------------------------------------------
# Module: Observability - CloudWatch Dashboard + Alarms
# -----------------------------------------------------------------------------
module "observability" {
  source = "./modules/observability"

  environment            = var.environment
  project_name           = var.project_name
  aws_region             = var.aws_region
  state_machine_name     = module.orchestration.state_machine_name
  codebuild_project_name = module.codebuild.project_name
  state_table_name       = module.storage.state_table_name
  sns_alarm_topic_arn    = var.sns_escalation_topic_arn
}

# -----------------------------------------------------------------------------
# Module: VPC - optional new VPC (public + private subnets, NAT) for the web app
# -----------------------------------------------------------------------------
module "vpc" {
  source = "./modules/vpc"
  count  = var.create_vpc ? 1 : 0

  project_name       = var.project_name
  environment        = var.environment
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones
}

locals {
  # Resolve networking from the new VPC module or from provided existing IDs.
  web_vpc_id             = var.create_vpc ? module.vpc[0].vpc_id : var.existing_vpc_id
  web_public_subnet_ids  = var.create_vpc ? module.vpc[0].public_subnet_ids : var.public_subnet_ids
  web_private_subnet_ids = var.create_vpc ? module.vpc[0].private_subnet_ids : var.private_subnet_ids
}

# -----------------------------------------------------------------------------
# Module: Web App - ECR -> image build/push -> ECS/Fargate + ALB
# -----------------------------------------------------------------------------
module "webapp" {
  source = "./modules/webapp"
  count  = var.deploy_webapp ? 1 : 0

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region

  vpc_id             = local.web_vpc_id
  public_subnet_ids  = local.web_public_subnet_ids
  private_subnet_ids = local.web_private_subnet_ids
  allowed_web_cidrs  = var.allowed_web_cidrs

  context_dir = path.module
  image_tag   = var.image_tag

  artifacts_bucket     = module.storage.artifacts_bucket_name
  artifacts_bucket_arn = module.storage.artifacts_bucket_arn
  results_bucket       = module.storage.results_bucket_name
  results_bucket_arn   = module.storage.results_bucket_arn
  state_table          = module.storage.state_table_name
  state_table_arn      = module.storage.state_table_arn

  transformer_project_name   = module.codebuild.transformer_project_name
  transformation_timeout_min = 30
  kms_key_arn                = aws_kms_key.main.arn

  # Tier 1: the web app drives the closed-loop pipeline via Step Functions.
  state_machine_arn = module.orchestration.state_machine_arn
}
