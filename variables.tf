variable "project_name" {
  description = "Unique project name for all resources"
  type        = string
}

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "embedding_dimensions" {
  description = "Vector dimensions for Titan Embeddings"
  type        = number
  default     = 1024
}

variable "chunk_size" {
  description = "Size of text chunks in tokens"
  type        = number
  default     = 512
}

variable "chunk_overlap_percentage" {
  description = "Overlap percentage between chunks"
  type        = number
 default     = 20
}

variable "environment" {
  description = "Environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "api_stage_name" {
  description = "API Gateway stage name"
  type        = string
  default     = "v1"
}

variable "enable_api_gateway" {
  description = "Enable API Gateway for query endpoint"
  type        = bool
  default     = true
}