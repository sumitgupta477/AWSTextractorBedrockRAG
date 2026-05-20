# ============================================================
# IAM Role for Lambda Functions
# ============================================================
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

resource "aws_iam_role" "lambda_general_role" {
  name = "${var.project_name}-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "lambda_basic_execution" {
  name = "${var.project_name}-lambda-basic-execution"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ]
      Resource = "arn:${data.aws_partition.current.partition}:logs:*:*:*"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_general_role.name
  policy_arn = aws_iam_policy.lambda_basic_execution.arn
}

# ============================================================
# IAM Role for Step Functions
# ============================================================
resource "aws_iam_role" "sfn_role" {
  name = "${var.project_name}-sfn-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "states.${var.aws_region}.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "sfn_policy" {
  name = "${var.project_name}-sfn-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.chunking.arn
      },
      {
        Effect = "Allow"
        Action = [
          "textract:StartDocumentTextDetection",
          "textract:GetDocumentTextDetection"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [
          aws_s3_bucket.raw_documents.arn,
          "${aws_s3_bucket.raw_documents.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = ["bedrock:StartIngestionJob"]
        Resource = aws_bedrockagent_knowledge_base.rag.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject"
        ]
        Resource = [
          aws_s3_bucket.textract_output.arn,
          "${aws_s3_bucket.textract_output.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "sfn_policy" {
  role       = aws_iam_role.sfn_role.name
  policy_arn = aws_iam_policy.sfn_policy.arn
}

# ============================================================
# IAM Role for Bedrock Knowledge Base
# ============================================================
resource "aws_iam_role" "bedrock_kb_role" {
  name = "${var.project_name}-bedrock-kb-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "bedrock_kb_policy" {
  name = "${var.project_name}-bedrock-kb-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "bedrock:InvokeModel"
        Resource = "arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.raw_documents.arn,
          "${aws_s3_bucket.raw_documents.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3vectors:PutVectorIndex",
          "s3vectors:GetVectorIndex",
          "s3vectors:QueryVectors",
          "s3vectors:PutVectors"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "bedrock_kb_policy" {
  role       = aws_iam_role.bedrock_kb_role.name
  policy_arn = aws_iam_policy.bedrock_kb_policy.arn
}