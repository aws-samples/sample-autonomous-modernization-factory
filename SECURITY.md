# Security

## Reporting a Vulnerability

If you discover a potential security issue in this project, we ask that you notify AWS Security via our
[vulnerability reporting page](https://aws.amazon.com/security/vulnerability-reporting/).
Please do **not** create a public GitHub issue.

## AWS Services Used

This pattern deploys and interacts with the following AWS services:

- AWS Step Functions (workflow orchestration)
- AWS CodeBuild (AWS Transform, build/test validation, AI remediation)
- Amazon Bedrock (AI-driven code remediation)
- Amazon ECS on AWS Fargate + Elastic Load Balancing (web app)
- Amazon Elastic Container Registry (web app image)
- Amazon S3 (uploads, results, audit artifacts; SSE-KMS encrypted)
- Amazon DynamoDB (per-run state, 30-day TTL)
- Amazon CloudWatch (logs, metrics, dashboard)
- AWS IAM (service roles and policies)

## Design Principles

- **Customer code never runs in the web tier.** All transformation, build, and remediation run inside ephemeral, IAM-scoped AWS CodeBuild sandboxes.
- **Least-privilege roles.** Each CodeBuild role is scoped to its run's S3 prefixes; only the remediator role is granted `bedrock:InvokeModel`.
- **Encryption at rest with a customer-managed key.** A shared KMS CMK (with automatic rotation) encrypts DynamoDB, the CodeBuild projects, ECR, and the CloudWatch log groups; S3 uses SSE-KMS. Downloads are served with short-lived SigV4 presigned URLs.
- **Output escaping in the web UI.** All dynamic values rendered by the frontend (progress entries, file paths, diff content) pass through HTML escaping before insertion into the DOM.
- **Bounded automation.** The remediation loop is capped by `max_retry_attempts` and always terminates in `COMPLETED`, `ESCALATED`, or `FAILED`.

## Known Security Considerations

The following are accepted trade-offs for this sample pattern.

| # | Item | Rationale |
|---|------|-----------|
| D1 | Remediator `bedrock:InvokeModel` uses `foundation-model/*` and `inference-profile/*` resource ARNs | Cross-region inference profiles route to foundation models across Regions; resource-level scoping to a single model ARN is not sufficient for on-demand profile invocation |
| D2 | Uploaded archives are user-supplied | Archives are validated (type/size/zip integrity) and only extracted and executed inside isolated CodeBuild sandboxes, never in the web tier |
| D3 | The ALB serves HTTPS with a self-signed certificate (HTTP redirects to HTTPS) | A clone-and-run sample has no domain/ACM-issued cert, so it generates a self-signed one; browsers will warn. Replace with an ACM certificate for your domain in production |
| D4 | The ALB is internet-facing with no WAF/Shield | Ingress defaults to the VPC CIDR (`allowed_web_cidrs` empty = private); set it to your network to expose the app. Add AWS WAF/Shield or make the ALB internal for production |
| D5 | ALB deletion protection is enabled by default | Set `alb_deletion_protection = false` before running `terraform destroy` to remove the ALB |
| D6 | S3 buckets and the ALB do not emit access logs | Add a dedicated log bucket and enable S3 server-access logging + ALB access logs for audit trails |
| D7 | The VPC has no flow logs or network firewall | Enable VPC Flow Logs (and AWS Network Firewall if required) for network-level visibility in production |
| D8 | ECR image tags are mutable | The sample re-pushes the `latest` tag; set immutable tags + digest pinning for production |
| D9 | IAM Access Analyzer and S3 MFA-delete are not configured | These are account/operationally scoped; enable them at the account level rather than per sample deploy |
| D10 | The Step Functions role uses `logs:*` on `*` and `s3:GetObject/PutObject` on the run buckets | The `logs:*` grant is required by the CodeBuild `.sync` integration's managed log-delivery; S3 access is scoped to the artifacts/results buckets |
| D11 | The transformer installs the AWS Transform CLI via `curl … \| bash` | This is the documented AWS install path for `atx`; there is no packaged equivalent for a public sample |
| D12 | The webapp and CodeBuild security groups allow all outbound (egress `0.0.0.0/0`) | Egress is NAT'd through the VPC and CodeBuild containers are short-lived; restrict egress to HTTPS/DNS and add VPC endpoints for S3/DynamoDB/CodeBuild in production |
| D13 | The Step Functions state machine has no AWS X-Ray tracing | Tracing is an observability aid, not a security control; add `tracing_configuration { enabled = true }` for end-to-end tracing in production |
| D14 | S3 buckets do not attach a bucket policy denying non-TLS (`aws:SecureTransport`) access | All access is via the AWS SDKs/console which default to HTTPS, and buckets are SSE-KMS encrypted with public access blocked; add an explicit `DenyInsecureTransport` bucket policy to enforce TLS-in-transit in production |

## Production Hardening Recommendations

1. Terminate TLS at the ALB with AWS Certificate Manager and redirect HTTP to HTTPS.
2. Add authentication in front of the web app (for example, Amazon Cognito or an OIDC proxy).
3. Restrict the ALB with AWS WAF and tightened security groups, or make it internal.
4. Add an Amazon SNS escalation topic (`sns_escalation_topic_arn`) so `ESCALATED` runs notify a human.
5. Scope the remediator model permission to specific inference-profile ARNs once your model is fixed.
6. Enable AWS CloudTrail and review CodeBuild and Step Functions execution logs.
7. Restrict security-group egress to required destinations (HTTPS/DNS) and add VPC endpoints for Amazon S3, Amazon DynamoDB, and AWS CodeBuild to keep traffic on the AWS network.
8. Attach a `DenyInsecureTransport` bucket policy to the S3 buckets to enforce TLS in transit.

## Shared Responsibility Model

This sample follows the [AWS Shared Responsibility Model](https://aws.amazon.com/compliance/shared-responsibility-model/).
AWS manages the security **of** the cloud infrastructure. You are responsible for security **in** the cloud,
including IAM policies, encryption configuration, network access controls, and operational procedures
deployed by this pattern.
