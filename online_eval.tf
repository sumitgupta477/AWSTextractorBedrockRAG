# ============================================================
# Online evaluation: nightly LLM judging of real answers
#
# The query Lambda saves a random share (var.online_eval_sample_rate) of
# answered questions, with their answers and retrieved passages, to a
# private, encrypted bucket. Once a day a judge Lambda grades them with a
# Bedrock model for faithfulness and relevance and publishes the scores as
# CloudWatch metrics plus a report of the worst answers.
#
# Privacy: samples contain real user questions. They are kept only for
# var.online_eval_retention_days and the bucket blocks all public access.
# Set enable_online_eval = false to store nothing.
# ============================================================
locals {
  online_eval_namespace = "RAG/${var.project_name}-${var.environment}"
}

resource "aws_s3_bucket" "eval_samples" {
  count         = var.enable_online_eval ? 1 : 0
  bucket        = "${var.project_name}-${var.environment}-eval-samples"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "eval_samples" {
  count                   = var.enable_online_eval ? 1 : 0
  bucket                  = aws_s3_bucket.eval_samples[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "eval_samples" {
  count  = var.enable_online_eval ? 1 : 0
  bucket = aws_s3_bucket.eval_samples[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "eval_samples" {
  count  = var.enable_online_eval ? 1 : 0
  bucket = aws_s3_bucket.eval_samples[0].id

  rule {
    id     = "expire-samples"
    status = "Enabled"
    filter {
      prefix = "samples/"
    }
    expiration {
      days = var.online_eval_retention_days
    }
  }

  rule {
    id     = "expire-reports"
    status = "Enabled"
    filter {
      prefix = "reports/"
    }
    expiration {
      days = 365
    }
  }
}

resource "aws_iam_role_policy" "lambda_online_eval" {
  count = var.enable_online_eval ? 1 : 0
  name  = "${var.project_name}-lambda-online-eval"
  role  = aws_iam_role.lambda_general_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = ["${aws_s3_bucket.eval_samples[0].arn}/samples/*", "${aws_s3_bucket.eval_samples[0].arn}/reports/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.eval_samples[0].arn
      }
      # bedrock:InvokeModel for the judge is already granted in iam.tf.
    ]
  })
}

data "archive_file" "judge_lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/judge"
  output_path = "${path.module}/judge.zip"
}

resource "aws_lambda_function" "judge" {
  count            = var.enable_online_eval ? 1 : 0
  filename         = data.archive_file.judge_lambda_zip.output_path
  source_code_hash = data.archive_file.judge_lambda_zip.output_base64sha256
  function_name    = "${var.project_name}-llm-judge"
  role             = aws_iam_role.lambda_general_role.arn
  handler          = "main.lambda_handler"
  runtime          = "python3.12"
  timeout          = 900
  memory_size      = 256

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      EVAL_SAMPLE_BUCKET  = aws_s3_bucket.eval_samples[0].bucket
      JUDGE_MODEL_ID      = var.judge_model_id
      METRIC_NAMESPACE    = local.online_eval_namespace
      PROJECT_NAME        = "${var.project_name}-${var.environment}"
      MAX_SAMPLES_PER_RUN = tostring(var.online_eval_max_samples)
    }
  }
}

resource "aws_cloudwatch_log_group" "judge_logs" {
  count             = var.enable_online_eval ? 1 : 0
  name              = "/aws/lambda/${aws_lambda_function.judge[0].function_name}"
  retention_in_days = 90
}

# Daily schedule (default 07:00 UTC = early morning in North America)
resource "aws_cloudwatch_event_rule" "judge_schedule" {
  count               = var.enable_online_eval ? 1 : 0
  name                = "${var.project_name}-${var.environment}-llm-judge"
  description         = "Grade yesterday's sampled answers with the LLM judge"
  schedule_expression = var.online_eval_schedule
}

resource "aws_cloudwatch_event_target" "judge" {
  count = var.enable_online_eval ? 1 : 0
  rule  = aws_cloudwatch_event_rule.judge_schedule[0].name
  arn   = aws_lambda_function.judge[0].arn
}

resource "aws_lambda_permission" "allow_eventbridge_judge" {
  count         = var.enable_online_eval ? 1 : 0
  statement_id  = "AllowEventBridgeInvokeJudge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.judge[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.judge_schedule[0].arn
}

# Alarm when yesterday's real answers were less faithful than the bar
resource "aws_cloudwatch_metric_alarm" "online_faithfulness" {
  count               = var.enable_online_eval ? 1 : 0
  alarm_name          = "${var.project_name}-${var.environment}-faithfulness-low"
  alarm_description   = "Average LLM-judged faithfulness of sampled live answers fell below ${var.faithfulness_alarm_threshold}/5. See the latest report under reports/ in the eval-samples bucket."
  namespace           = local.online_eval_namespace
  metric_name         = "Faithfulness"
  dimensions          = { Project = "${var.project_name}-${var.environment}" }
  statistic           = "Average"
  period              = 86400
  evaluation_periods  = 1
  threshold           = var.faithfulness_alarm_threshold
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}
