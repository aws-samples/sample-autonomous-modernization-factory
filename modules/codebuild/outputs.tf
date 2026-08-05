output "project_name" {
  description = "Name of the validator CodeBuild project"
  value       = aws_codebuild_project.validator.name
}

output "project_arn" {
  description = "ARN of the validator CodeBuild project"
  value       = aws_codebuild_project.validator.arn
}

output "role_arn" {
  description = "ARN of the validator CodeBuild service role"
  value       = aws_iam_role.codebuild.arn
}

output "transformer_project_name" {
  description = "Name of the transformer CodeBuild project"
  value       = aws_codebuild_project.transformer.name
}

output "transformer_role_arn" {
  description = "ARN of the transformer CodeBuild service role"
  value       = aws_iam_role.transformer.arn
}

output "remediator_project_name" {
  description = "Name of the remediator CodeBuild project"
  value       = aws_codebuild_project.remediator.name
}

output "remediator_role_arn" {
  description = "ARN of the remediator CodeBuild service role"
  value       = aws_iam_role.remediator.arn
}
