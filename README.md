# AWSTextractorBedrockRAG
Repo which shows how to do Rag on Bedrock By Using Textract and S3 Vector to Do MLOps

# How to deploy

# 1. Clone this structure
mkdir terraform-rag-pipeline && cd terraform-rag-pipeline
# Copy all files above

# 2. Set up the Python code
mkdir -p lambda/sfn-trigger lambda/chunking lambda/query
# Copy the Python files into their respective directories

# 3. Deploy with Terraform
terraform init
terraform plan
terraform apply -auto-approve

# 4. After deployment, enable Bedrock models (once per account)
aws bedrock list-foundation-models --region us-east-1
# Ensure model access is enabled for:
# - amazon.titan-embed-text-v2:0
# - amazon.titan-text-premier-v1:0

# 5. Sync the knowledge base (initial sync)
aws bedrock-agent start-ingestion-job \
    --knowledge-base-id $(terraform output -raw knowledge_base_id) \
    --data-source-id $(aws bedrock-agent list-data-sources --knowledge-base-id $(terraform output -raw knowledge_base_id) --query 'dataSourceSummaries[0].dataSourceId' --output text) \
    --ingestion-job-name "initial-sync"
