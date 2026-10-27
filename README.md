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

`POST /query` on API Gateway (API-key protected and rate-limited) → `query` Lambda → Bedrock **RetrieveAndGenerate**, which retrieves the most relevant chunks and generates an answer with citations.

## What this demonstrates

- RAG architecture on Amazon Bedrock Knowledge Bases with S3 Vectors as a low-cost vector store
- Document intelligence: async Textract OCR for multi-page PDFs and scanned images
- Workflow orchestration with Step Functions (polling, retries, payload-size-safe state handling)
- Custom chunking with source/page metadata for traceable, citable answers
- API-key authentication (Lambda authorizer) and throttling on the public endpoint
- Automated quality evaluation: keyword recall, citation accuracy, abstention and latency
- Least-privilege IAM per component, CloudWatch logging, encrypted and versioned S3 buckets
- Infrastructure as code with Terraform

## Design decisions

| Decision | Why |
|---|---|
| **S3 Vectors** instead of OpenSearch Serverless | OpenSearch Serverless bills a minimum number of OCUs around the clock (hundreds of dollars a month even when idle). S3 Vectors charges only for storage and queries, so an idle demo costs close to nothing. |
| **Async Textract + Step Functions polling** | The synchronous Textract API only handles single-page images. The async API handles multi-page PDFs, and Step Functions gives retries and back-off without a long-running Lambda. |
| **Custom chunking Lambda** instead of Knowledge Base default chunking | Keeps the source document and page range on every chunk, so answers can cite exactly where they came from. |
| **Lambda authorizer + throttling** on the query API | HTTP APIs have no built-in API keys. A tiny authorizer checks an `x-api-key` header (results cached 5 minutes), and stage throttling caps requests per second, so a leaked URL can't run up the Bedrock bill. |
| **Only the job status kept in state** | Textract responses can exceed the 256 KB Step Functions payload limit; the chunking Lambda reads results directly from Textract instead. |

## Cost

Everything is serverless and pay-per-use, so there is no idle cost apart from small S3 storage charges. The main per-document costs are:

| Component | What drives cost |
|---|---|
| Textract text detection | Per page processed |
| Titan Text Embeddings v2 | Per input token embedded (one-time per chunk) |
| Generation model | Input + output tokens per query |
| S3 Vectors | Vector storage and query calls |
| Lambda, Step Functions, API Gateway | Typically pennies at demo scale |

Check the current [Bedrock](https://aws.amazon.com/bedrock/pricing/) and [Textract](https://aws.amazon.com/textract/pricing/) pricing pages for rates in your region. To see what a real run cost, filter Cost Explorer by the `Project` tag that every resource carries.

## Observability

- **Step Functions** execution history shows each stage, its duration and any retries.
- **CloudWatch Logs** for every Lambda (`/aws/lambda/<project>-*`).
- **Bedrock** publishes `Invocations`, `InvocationLatency` and token-count metrics in CloudWatch, useful for tracking query latency and cost per question.

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
  -H "x-api-key: $(terraform output -raw api_key)" \
  -d '{"query": "What does this document say about ...?"}'
```

Requests without a valid key get `403 Forbidden`.

## Evaluate answer quality

`eval/run_eval.py` sends a set of test questions to the live endpoint and scores the answers:

| Metric | What it checks |
|---|---|
| Keyword recall | Share of expected facts present in the answer |
| Citation hit rate | Whether the answer cites the document that actually holds the answer |
| Abstention rate | For out-of-scope questions, whether the model declines instead of making something up |
| Latency (p50 / max) | End-to-end response time |

Edit `eval/questions.jsonl` to match your documents, then run:

```bash
export RAG_API_URL=$(terraform output -raw api_gateway_endpoint)
export RAG_API_KEY=$(terraform output -raw api_key)
python eval/run_eval.py eval/questions.jsonl --out eval/results.json
```

It exits non-zero if any case fails, so it can gate a CI pipeline. Re-run it after changing the chunk size, embedding settings or generation model to compare results.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `project_name` | (required) | Prefix for all resource names |
| `environment` | `dev` | Environment suffix |
| `aws_region` | `us-east-1` | Deployment region |
| `generation_model_id` | `amazon.titan-text-premier-v1:0` | Model that writes the answers |
| `chunk_size_chars` / `chunk_overlap_chars` | `2000` / `200` | Chunking behaviour |
| `embedding_dimensions` | `1024` | Titan v2 embedding size |
| `api_throttle_rate_limit` / `api_throttle_burst_limit` | `5` / `10` | Requests per second and burst allowed on the query API |

## Clean up

```bash
terraform destroy -var project_name=my-rag
```
