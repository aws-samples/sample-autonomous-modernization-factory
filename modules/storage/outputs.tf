output "artifacts_bucket_name" {
  description = "Name of the artifacts S3 bucket"
  value       = aws_s3_bucket.artifacts.bucket
}

output "artifacts_bucket_arn" {
  description = "ARN of the artifacts S3 bucket"
  value       = aws_s3_bucket.artifacts.arn
}

output "results_bucket_name" {
  description = "Name of the results S3 bucket"
  value       = aws_s3_bucket.results.bucket
}

output "results_bucket_arn" {
  description = "ARN of the results S3 bucket"
  value       = aws_s3_bucket.results.arn
}

output "state_table_name" {
  description = "Name of the DynamoDB run-state table"
  value       = aws_dynamodb_table.state.name
}

output "state_table_arn" {
  description = "ARN of the DynamoDB run-state table"
  value       = aws_dynamodb_table.state.arn
}
