# ============================================================
# RAG eval monitoring: CloudWatch metrics, drift alarm, alerts
# ============================================================
# The GitHub Actions eval (evals/) publishes its scores and a DriftDetected
# metric to CloudWatch. This file creates:
#   - an IAM role GitHub Actions assumes through OIDC to publish them and to
#     call the Bedrock judge model
#   - an alarm that fires when a run detects drift
#   - an SNS topic (with optional email subscription) for the alerts
#   - a dashboard showing every score over time
#
# After `terraform apply`, store the `eval_role_arn` output as the
# AWS_EVAL_ROLE_ARN secret in the GitHub repository.

variable "eval_alert_email" {
  description = "Email address for LLM eval drift alerts (leave empty to skip; you can subscribe Slack or PagerDuty to the SNS topic instead)"
  type        = string
  default     = ""
}

variable "eval_github_repo" {
  description = "GitHub repository (owner/name, exact case) allowed to publish eval metrics"
  type        = string
  default     = "sumitgupta477/AWSTextractorBedrockRAG"
}

variable "eval_cloudwatch_namespace" {
  description = "CloudWatch namespace for eval metrics (must match EVAL_CLOUDWATCH_NAMESPACE in the workflow)"
  type        = string
  default     = "LLMEval/TextractBedrockRAG"
}

variable "eval_grader_model_id" {
  description = "Bedrock model the eval uses as its judge (matches EVAL_GRADER in evals/promptfooconfig.yaml)"
  type        = string
  default     = "amazon.nova-pro-v1:0"
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub Actions OIDC provider. Set to false if this AWS account already has one (only one is allowed per account)."
  type        = bool
  default     = true
}

locals {
  eval_namespace       = var.eval_cloudwatch_namespace
  eval_quality_metrics = ["correctness", "faithfulness", "citations", "no_hallucination", "pass_rate"]
  github_oidc_url = "https://token.actions.githubusercontent.com"
  github_oidc_arn = (var.create_github_oidc_provider
    ? aws_iam_openid_connect_provider.github[0].arn
  : data.aws_iam_openid_connect_provider.github[0].arn)
}

# ---------- GitHub Actions OIDC role ----------
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_oidc_provider ? 1 : 0
  url            = local.github_oidc_url
  client_id_list = ["sts.amazonaws.com"]
  # AWS no longer validates these for GitHub, but older provider versions require the field.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1
  url   = local.github_oidc_url
}

resource "aws_iam_role" "eval_publisher" {
  name = "${var.project_name}-rag-eval"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.github_oidc_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
        StringLike   = { "token.actions.githubusercontent.com:sub" = "repo:${var.eval_github_repo}:*" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "eval_publisher" {
  name = "rag-eval"
  role = aws_iam_role.eval_publisher.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublishEvalMetrics"
        Effect    = "Allow"
        Action    = "cloudwatch:PutMetricData"
        Resource  = "*" # PutMetricData has no resource-level permissions; the condition scopes it
        Condition = { StringEquals = { "cloudwatch:namespace" = local.eval_namespace } }
      },
      {
        Sid    = "InvokeJudgeModel"
        Effect = "Allow"
        Action = "bedrock:InvokeModel" # also covers the Converse API
        Resource = [
          "arn:aws:bedrock:*::foundation-model/${var.eval_grader_model_id}",
          # Cross-region inference profiles, e.g. us.amazon.nova-pro-v1:0
          "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/*${var.eval_grader_model_id}",
        ]
      },
    ]
  })
}

# ---------- Alerts ----------
resource "aws_sns_topic" "eval_alerts" {
  name = "${var.project_name}-rag-eval-alerts"
}

resource "aws_sns_topic_subscription" "eval_alert_email" {
  count     = var.eval_alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.eval_alerts.arn
  protocol  = "email"
  endpoint  = var.eval_alert_email # AWS emails a confirmation link; click it to start receiving alerts
}

resource "aws_cloudwatch_metric_alarm" "eval_drift" {
  alarm_name          = "${var.project_name}-rag-eval-drift"
  alarm_description   = "A RAG eval run scored below the committed baseline (evals/baseline.json). Check the GitHub Actions run summary for which metrics and tests regressed."
  namespace           = local.eval_namespace
  metric_name         = "DriftDetected"
  statistic           = "Maximum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  # Evals run weekly, so most days have no data; that is not an alert.
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.eval_alerts.arn]
  ok_actions         = [aws_sns_topic.eval_alerts.arn]
}

# ---------- Dashboard ----------
resource "aws_cloudwatch_dashboard" "eval" {
  dashboard_name = "${var.project_name}-rag-eval"
  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 16
        height = 7
        properties = {
          title   = "Eval scores (0-1)"
          region  = var.aws_region
          view    = "timeSeries"
          stat    = "Average"
          period  = 86400
          metrics = [for m in local.eval_quality_metrics : [local.eval_namespace, m]]
          yAxis   = { left = { min = 0, max = 1 } }
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 0
        width  = 8
        height = 7
        properties = {
          title   = "Drift detected (1 = yes)"
          region  = var.aws_region
          view    = "bar"
          stat    = "Maximum"
          period  = 86400
          metrics = [[local.eval_namespace, "DriftDetected"]]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 7
        width  = 12
        height = 6
        properties = {
          title   = "Error rate"
          region  = var.aws_region
          view    = "timeSeries"
          stat    = "Average"
          period  = 86400
          metrics = [[local.eval_namespace, "error_rate"]]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 7
        width  = 12
        height = 6
        properties = {
          title   = "Average answer latency (ms)"
          region  = var.aws_region
          view    = "timeSeries"
          stat    = "Average"
          period  = 86400
          metrics = [[local.eval_namespace, "avg_latency_ms"]]
        }
      },
    ]
  })
}

output "eval_role_arn" {
  description = "Store as the AWS_EVAL_ROLE_ARN secret in GitHub so the eval workflow can call the judge model and publish metrics"
  value       = aws_iam_role.eval_publisher.arn
}

output "eval_alerts_topic_arn" {
  description = "SNS topic for drift alerts; subscribe Slack (AWS Chatbot) or PagerDuty here"
  value       = aws_sns_topic.eval_alerts.arn
}

output "eval_dashboard_url" {
  value = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards/dashboard/${aws_cloudwatch_dashboard.eval.dashboard_name}"
}
