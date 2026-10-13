# AWS Textract + Bedrock RAG Pipeline

An event-driven, fully serverless **Retrieval-Augmented Generation (RAG)** pipeline on AWS. Drop a PDF or scanned document into S3 and it is automatically OCR'd, chunked, embedded and indexed; ask questions through a REST API and get answers grounded in your documents, with citations. All infrastructure is defined in Terraform.

<img width="791" height="461" alt="Architecture: S3 upload triggers Step Functions, which runs Textract, chunking and Bedrock Knowledge Base sync; queries go through API Gateway and Lambda to Bedrock RetrieveAndGenerate" src="https://github.com/user-attachments/assets/7941fe4d-314b-41dc-bccf-39a49b0e9ef3" />

## How it works

**Ingestion (runs automatically on every upload)**

1. A document lands in `s3://<raw-bucket>/documents/`.
2. An S3 event invokes the `sfn-trigger` Lambda, which starts a Step Functions execution.
3. Step Functions starts an **async Textract** text-detection job and polls until it finishes (with retries and back-off on throttling).
4. The **chunking Lambda** reads every page of the Textract result, splits the text into overlapping chunks on paragraph/sentence boundaries, and writes each chunk to S3 with a `.metadata.json` file recording the source document and page range.
5. Step Functions starts a **Bedrock Knowledge Base ingestion job**, which embeds the chunks with **Amazon Titan Text Embeddings v2** and stores the vectors in **Amazon S3 Vectors**.

**Query**

`POST /query` on API Gateway → `query` Lambda → Bedrock **RetrieveAndGenerate**, which retrieves the most relevant chunks and generates an answer with citations.

## What this demonstrates

- RAG architecture on Amazon Bedrock Knowledge Bases with S3 Vectors as a low-cost vector store
- Document intelligence: async Textract OCR for multi-page PDFs and scanned images
- Workflow orchestration with Step Functions (polling, retries, payload-size-safe state handling)
- Custom chunking with source/page metadata for traceable, citable answers
- Least-privilege IAM per component, CloudWatch logging, encrypted and versioned S3 buckets
- Infrastructure as code with Terraform

## Deploy

Prerequisites: an AWS account, the AWS CLI configured for `us-east-1`, and Terraform ≥ 1.5 (AWS provider ~> 6.27 for S3 Vectors support).

1. In the Bedrock console, enable model access for **Titan Text Embeddings V2** and for the generation model you plan to use (default `amazon.titan-text-premier-v1:0`; override with `-var generation_model_id=...`, which also accepts an inference-profile ARN).
2. Deploy:

   ```bash
   terraform init
   terraform apply -var project_name=my-rag
   ```

## Try it

```bash
# Upload a document; ingestion starts automatically
aws s3 cp sample.pdf s3://$(terraform output -raw raw_documents_bucket)/documents/sample.pdf

# Watch the pipeline run
aws stepfunctions list-executions \
  --state-machine-arn $(terraform output -raw sfn_state_machine_arn)

# Ask a question once the execution has succeeded
curl -X POST "$(terraform output -raw api_gateway_endpoint)" \
  -H "Content-Type: application/json" \
  -d '{"query": "What does this document say about ...?"}'
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `project_name` | (required) | Prefix for all resource names |
| `environment` | `dev` | Environment suffix |
| `aws_region` | `us-east-1` | Deployment region |
| `generation_model_id` | `amazon.titan-text-premier-v1:0` | Model that writes the answers |
| `chunk_size_chars` / `chunk_overlap_chars` | `2000` / `200` | Chunking behaviour |
| `embedding_dimensions` | `1024` | Titan v2 embedding size |

## Clean up

```bash
terraform destroy -var project_name=my-rag
```
