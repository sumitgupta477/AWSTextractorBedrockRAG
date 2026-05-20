# ============================================================
# Bedrock Knowledge Base with S3 Vectors Storage
# ============================================================
resource "aws_bedrockagent_knowledge_base" "rag" {
  name     = "${var.project_name}-${var.environment}-kb"
  role_arn = aws_iam_role.bedrock_kb_role.arn

  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = "arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0"
      embedding_model_configuration {
        bedrock_embedding_model_configuration {
          dimensions           = var.embedding_dimensions
          embedding_data_type  = "FLOAT32"
        }
      }
    }
  }

  storage_configuration {
    type = "S3_VECTORS"
    s3_vectors_configuration {
      index_arn = aws_s3vectors_index.bedrock_index.index_arn
    }
  }

  depends_on = [aws_s3vectors_index.bedrock_index]

  tags = {
    Name        = "${var.project_name}-kb"
    Environment = var.environment
  }
}

# ============================================================
# Data Source for the Knowledge Base (points to S3 raw documents)
# ============================================================
resource "aws_bedrockagent_data_source" "rag_data_source" {
  knowledge_base_id = aws_bedrockagent_knowledge_base.rag.knowledge_base_id
  name              = "${var.project_name}-${var.environment}-data-source"
  data_deletion_policy = "RETAIN"

  data_source_configuration {
    type = "S3"
    s3_configuration {
      bucket_arn = aws_s3_bucket.raw_documents.arn
      inclusion_prefixes = ["documents/"]
    }
  }

  vector_ingestion_configuration {
    chunking_configuration {
      chunking_strategy = "FIXED_SIZE"
      fixed_size_chunking_configuration {
        max_tokens = var.chunk_size
        overlap_percentage = var.chunk_overlap_percentage
      }
    }
  }
}