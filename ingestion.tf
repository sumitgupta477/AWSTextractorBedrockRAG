# ============================================================
# Lambda Function: Chunking
# ============================================================
data "archive_file" "chunking_lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/chunking"
  output_path = "${path.module}/chunking.zip"
}

resource "aws_lambda_function" "chunking" {
  filename         = data.archive_file.chunking_lambda_zip.output_path
  function_name    = "${var.project_name}-chunking"
  role             = aws_iam_role.lambda_general_role.arn
  handler          = "main.lambda_handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 512

  environment {
    variables = {
      SOURCE_BUCKET     = aws_s3_bucket.textract_output.id
    }
  }
}

# ============================================================
# Step Functions State Machine Definition
# ============================================================
resource "aws_sfn_state_machine" "ingestion_pipeline" {
  name     = "${var.project_name}-${var.environment}-ingestion-pipeline"
  role_arn = aws_iam_role.sfn_role.arn
  type     = "STANDARD"

  definition = templatefile("${path.module}/state_machines/ingestion_pipeline.asl.json", {
    chunking_function_arn = aws_lambda_function.chunking.arn
    knowledge_base_id     = aws_bedrockagent_knowledge_base.rag.knowledge_base_id
    textract_output_bucket = aws_s3_bucket.textract_output.id
  })
}

# ============================================================
# Lambda Function: S3 Event to Step Functions Trigger
# ============================================================
data "archive_file" "sfn_trigger_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/sfn-trigger"
  output_path = "${path.module}/sfn-trigger.zip"
}

resource "aws_lambda_function" "sfn_trigger" {
  filename         = data.archive_file.sfn_trigger_zip.output_path
  function_name    = "${var.project_name}-sfn-trigger"
  role             = aws_iam_role.lambda_general_role.arn
  handler          = "main.lambda_handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 128

  environment {
    variables = {
      STATE_MACHINE_ARN = aws_sfn_state_machine.ingestion_pipeline.arn
    }
  }
}

resource "aws_cloudwatch_log_group" "sfn_trigger_logs" {
  name              = "/aws/lambda/${aws_lambda_function.sfn_trigger.function_name}"
  retention_in_days = 14
}

resource "aws_lambda_permission" "allow_s3" {
  statement_id  = "AllowExecutionFromS3"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sfn_trigger.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.raw_documents.arn
}

# ============================================================
# S3 Event Notification (Triggers Step Functions)
# ============================================================
resource "aws_s3_bucket_notification" "raw_documents_notification" {
  bucket = aws_s3_bucket.raw_documents.id
  lambda_function {
    lambda_function_arn = aws_lambda_function.sfn_trigger.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "documents/"
  }
}