# =============================================================================
# Outputs - Modernization Factory
# =============================================================================

output "orchestration_state_machine_arn" {
  description = "ARN of the Step Functions state machine (entry point for triggering modernization runs)"
  value       = module.orchestration.state_machine_arn
}

output "artifacts_bucket" {
  description = "S3 bucket for source code and transformed artifacts"
  value       = module.storage.artifacts_bucket_name
}

output "results_bucket" {
  description = "S3 bucket for build/test results and reports"
  value       = module.storage.results_bucket_name
}

output "state_table" {
  description = "DynamoDB table tracking per-application modernization state"
  value       = module.storage.state_table_name
}

output "codebuild_project_name" {
  description = "CodeBuild project name for build/test validation"
  value       = module.codebuild.project_name
}

output "dashboard_url" {
  description = "CloudWatch Dashboard URL for pipeline monitoring"
  value       = module.observability.dashboard_url
}

output "metric_namespace" {
  description = "Custom metric namespace for agents to publish to"
  value       = module.observability.metric_namespace
}

# --- Web App (modernization-web-app) ---
output "transformer_project_name" {
  description = "CodeBuild project that runs the AWS Transform CLI (atx)"
  value       = module.codebuild.transformer_project_name
}

output "webapp_url" {
  description = "Public URL (ALB DNS) of the modernization web app, if deployed"
  value       = var.deploy_webapp ? "https://${module.webapp[0].alb_dns_name}" : ""
}

output "webapp_ecr_repository_url" {
  description = "ECR repository URL for the API image, if deployed"
  value       = var.deploy_webapp ? module.webapp[0].ecr_repository_url : ""
}

output "web_vpc_id" {
  description = "VPC used by the web app (created or reused)"
  value       = local.web_vpc_id
}
