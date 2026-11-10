# RAG evaluation and drift detection

This folder tests the deployed RAG pipeline end to end, from OCR and chunking through retrieval to the generated answer, against a **golden dataset**, and compares every run with a saved **baseline**. If answer quality slips, whether from a chunking change, a new generation or embedding model, or a silent model update, the check fails.

## What is measured

The test document is a fictional travel policy ([`sample_docs/northwind_travel_policy.pdf`](sample_docs/)) with invented, distinctive facts (82 CAD per diem, 0.71 CAD per km and so on), so a correct answer can only come from retrieval, not the model's general knowledge. [`tests.yaml`](tests.yaml) asks 13 answerable and 3 unanswerable questions.

| Metric | How it is scored | Applies to |
|---|---|---|
| `correctness` | The answer contains the key fact from the document | Answerable |
| `faithfulness` | **LLM judge** (`context-faithfulness`): every claim is supported by the chunks the API actually retrieved (threshold 0.7) | Answerable |
| `citations` | The response cites the Northwind policy document | Answerable |
| `no_hallucination` | **LLM judge** (`llm-rubric`): the answer says the documents don't cover it instead of inventing a policy | Unanswerable |
| `pass_rate`, `error_rate` | Share of runs passing every check, and share failing with API errors | All |

Latency is tracked too, as a warning.

## How drift detection works

1. **Baseline:** run the eval once on a known-good deployment and save the scores to `baseline.json` (committed, so changes are reviewed).
2. **Every run** (`drift_check.py`) compares the new scores with the baseline over the tests both runs share, and fails if any metric or the pass rate drops by more than **0.05**, or the error rate rises by more than 0.05.
3. It lists the questions that regressed and warns if the judge model or promptfoo version changed since the baseline.

The workflow (`.github/workflows/rag-eval-drift.yml`) runs **weekly** and on demand. Because it calls the live API, it tests what is deployed. Run it manually after each `terraform apply`.

## Run it locally

Requirements: the stack deployed, Node.js 22+ (promptfoo needs it), Python 3.9+, AWS credentials allowed to call `bedrock:InvokeModel` on the judge model (Amazon Nova Pro by default; enable model access in the Bedrock console).

```bash
# 1. One time: index the test document
./evals/upload_sample_doc.sh

# 2. Run the eval and compare with the baseline
export RAG_API_URL="$(terraform output -raw api_gateway_endpoint)"
export RAG_API_KEY="$(terraform output -raw api_key)"   # the API requires an x-api-key header
mkdir -p evals/results
npx promptfoo@0.125.1 eval -c evals/promptfooconfig.yaml --repeat 2 -o evals/results/latest.json
python3 evals/drift_check.py evals/results/latest.json
```

### Create or update the baseline

Do this the first time, and again whenever you **intentionally** change behaviour (new model, new chunk size, edited dataset):

```bash
python3 evals/drift_check.py evals/results/latest.json --update-baseline
git add evals/baseline.json
```

Until `baseline.json` exists, the check prints a warning and passes.

## CloudWatch alerts

Every run publishes its scores, plus a `DriftDetected` metric (1 or 0), to CloudWatch namespace `LLMEval/TextractBedrockRAG`. [`eval_monitoring.tf`](../eval_monitoring.tf) creates:

- an **alarm** that fires when a run detects drift, and notifies an **SNS topic** (email, or Slack/PagerDuty subscribed to the topic)
- a **dashboard** with each score, error rate and answer latency over time
- the **IAM role** GitHub Actions assumes through OIDC: allowed to call the judge model and to `PutMetricData` in that namespace only

Setup:

```bash
terraform apply -var project_name=my-rag -var eval_alert_email=you@example.com   # confirm the email AWS sends you
terraform output -raw eval_role_arn   # save as the AWS_EVAL_ROLE_ARN GitHub secret
```

If your AWS account already has a GitHub OIDC provider, add `-var create_github_oidc_provider=false`.

To publish from your own machine: `python3 evals/drift_check.py evals/results/latest.json --cloudwatch-namespace LLMEval/TextractBedrockRAG` (uses boto3 if installed, otherwise the AWS CLI).


**Cost:** about $0.10 a month for the alarm, or nothing within the free tier of 10 alarms. Custom metrics are billed by the hour, and only for hours in which data is sent, so a weekly run costs a fraction of a cent. The dashboard is free if the account has 3 or fewer custom dashboards, and $3 a month otherwise. Delete the dashboard resource if you don't need it. The eval's model calls (OpenAI or Bedrock) are billed separately.

## Configuration

| Setting | Default | How to change |
|---|---|---|
| API under test | none (required) | `RAG_API_URL` env var |
| API key | none (required) | `RAG_API_KEY` env var (`terraform output -raw api_key`) |
| Judge model | `bedrock:amazon.nova-pro-v1:0` | `EVAL_GRADER` env var, e.g. `bedrock:us.amazon.nova-pro-v1:0` for an inference profile, or `openai:chat:gpt-4o` |
| Drift tolerance | 0.05 | `drift_check.py --tolerance 0.03` |

For CI, store the `eval_role_arn` Terraform output as the `AWS_EVAL_ROLE_ARN` secret, the `api_key` output as the `RAG_API_KEY` secret, and the API URL as the `RAG_API_URL` repository variable (see [CloudWatch alerts](#cloudwatch-alerts)).

## Files

| File | Purpose |
|---|---|
| `promptfooconfig.yaml` | Eval definition: provider and judge model |
| `tests.yaml` | Golden questions with expected facts and checks |
| `provider.js` | Promptfoo provider that calls the deployed `/query` API and returns the answer, retrieved chunks and sources |
| `drift_check.py` | Baseline comparison and report (standard library only) |
| `baseline.json` | Saved scores from the last accepted run (create with `--update-baseline`) |
| `sample_docs/` | The test document and the script that generates it |
| `upload_sample_doc.sh` | Uploads the test document and waits for ingestion |
