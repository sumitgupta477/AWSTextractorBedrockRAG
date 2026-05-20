# ============================================================
# S3 Bucket for Raw Documents (Source)
# ============================================================
resource "aws_s3_bucket" "raw_documents" {
  bucket = "${var.project_name}-${var.environment}-raw-documents"
  force_destroy = true
  tags = {
    Name        = "${var.project_name}-raw-documents"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_versioning" "raw_documents" {
  bucket = aws_s3_bucket.raw_documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw_documents" {
  bucket = aws_s3_bucket.raw_documents.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ============================================================
# S3 Bucket for Textract Output
# ============================================================
resource "aws_s3_bucket" "textract_output" {
  bucket = "${var.project_name}-${var.environment}-textract-output"
  force_destroy = true
  tags = {
    Name        = "${var.project_name}-textract-output"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "textract_output" {
  bucket = aws_s3_bucket.textract_output.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ============================================================
# S3 Vectors Bucket and Index (Vector Storage)
# ============================================================
# S3 Vectors support in the Terraform AWS provider landed in v6.27.0
resource "aws_s3vectors_vector_bucket" "vectors" {
  bucket_name = "${var.project_name}-${var.environment}-vector-bucket"
  force_destroy = true
  tags = {
    Name        = "${var.project_name}-vectors"
    Environment = var.environment
  }
}

resource "aws_s3vectors_index" "bedrock_index" {
  bucket_name = aws_s3vectors_vector_bucket.vectors.bucket_name
  index_name  = "bedrock-knowledge-base-index"
  dimension   = var.embedding_dimensions
  distance_metric = "cosine"
  data_type   = "float32"
  tags = {
    Name        = "${var.project_name}-vector-index"
    Environment = var.environment
  }
}