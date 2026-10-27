"""Nightly LLM judge: grades a sample of real answers from the previous day.

Runs on a schedule. Reads the question/answer/passage samples the query
Lambda saved for one UTC day, asks a Bedrock model to rate each answer for
faithfulness (supported by the retrieved passages?) and relevance (does it
answer the question?), then:

  - emits one CloudWatch Embedded Metric Format line per graded answer, so
    Faithfulness / Relevance / LowFaithfulness metrics appear automatically
  - writes a daily report to s3://<bucket>/reports/YYYY/MM/DD.json listing the
    lowest-scoring answers for review

Event (optional): {"date": "YYYY-MM-DD"} to grade a specific day; defaults to yesterday.
"""

import json
import os
import random
import re
import time
from datetime import date, datetime, timedelta, timezone

import boto3

BUCKET = os.environ["EVAL_SAMPLE_BUCKET"]
JUDGE_MODEL_ID = os.environ["JUDGE_MODEL_ID"]
NAMESPACE = os.environ.get("METRIC_NAMESPACE", "RAG")
PROJECT = os.environ.get("PROJECT_NAME", "rag")
MAX_SAMPLES = int(os.environ.get("MAX_SAMPLES_PER_RUN", "200"))
LOW_FAITHFULNESS = int(os.environ.get("LOW_FAITHFULNESS_SCORE", "3"))

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime")

JUDGE_PROMPT = """You are grading a retrieval-augmented question-answering system.

QUESTION:
{question}

RETRIEVED PASSAGES:
{passages}

ANSWER:
{answer}

Rate the answer on two 1-5 scales:
- faithfulness: 5 = every claim in the answer is supported by the passages; 1 = mostly unsupported or contradicted.
- relevance: 5 = fully and directly answers the question; 1 = does not answer it.
If the answer declines to answer because the passages don't cover the question, rate faithfulness 5.

Respond with only JSON: {{"faithfulness": <1-5>, "relevance": <1-5>, "reason": "<one sentence>"}}"""


def lambda_handler(event, context):
    day = parse_day((event or {}).get("date"))
    keys = list_samples(day)
    if len(keys) > MAX_SAMPLES:
        keys = random.sample(keys, MAX_SAMPLES)

    graded, errors = [], 0
    for key in keys:
        sample = json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
        try:
            scores = judge(sample)
        except Exception as e:  # one bad sample shouldn't stop the run
            errors += 1
            print(json.dumps({"event": "judge_failed", "key": key, "error": repr(e)}))
            continue
        graded.append({"request_id": sample.get("request_id"), "key": key, **scores})
        print(json.dumps(emf_record(scores, sample.get("request_id"))))

    report = build_report(day, graded, errors, total_samples=len(keys))
    s3.put_object(
        Bucket=BUCKET,
        Key=f"reports/{day:%Y/%m/%d}.json",
        Body=json.dumps(report, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    print(json.dumps({"event": "judge_run_complete", **report["summary"]}))
    return report["summary"]


def parse_day(value):
    if value:
        return date.fromisoformat(value)
    return (datetime.now(timezone.utc) - timedelta(days=1)).date()


def list_samples(day):
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=f"samples/{day:%Y/%m/%d}/"):
        keys.extend(obj["Key"] for obj in page.get("Contents", []) if obj["Key"].endswith(".json"))
    return keys


def judge(sample, max_passages=8, max_chars=1500):
    passages = [p[:max_chars] for p in sample.get("passages", [])[:max_passages]]
    prompt = JUDGE_PROMPT.format(
        question=sample.get("question", ""),
        passages="\n---\n".join(passages) if passages else "(none retrieved)",
        answer=sample.get("answer", ""),
    )
    for attempt in range(3):
        try:
            response = bedrock.converse(
                modelId=JUDGE_MODEL_ID,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 300, "temperature": 0},
            )
            break
        except Exception as e:
            if "Throttling" not in type(e).__name__ or attempt == 2:
                raise
            time.sleep(2 ** attempt * 2)
    return parse_judgement(response["output"]["message"]["content"][0]["text"])


def parse_judgement(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON in judge response")
    data = json.loads(match.group(0))
    faithfulness, relevance = int(data["faithfulness"]), int(data["relevance"])
    if not (1 <= faithfulness <= 5 and 1 <= relevance <= 5):
        raise ValueError(f"scores out of range: {data}")
    return {"faithfulness": faithfulness, "relevance": relevance, "reason": str(data.get("reason", ""))}


def emf_record(scores, request_id):
    return {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": NAMESPACE,
                "Dimensions": [["Project"]],
                "Metrics": [
                    {"Name": "Faithfulness", "Unit": "None"},
                    {"Name": "Relevance", "Unit": "None"},
                    {"Name": "LowFaithfulness", "Unit": "Count"},
                ],
            }],
        },
        "Project": PROJECT,
        "Faithfulness": scores["faithfulness"],
        "Relevance": scores["relevance"],
        "LowFaithfulness": 1 if scores["faithfulness"] <= LOW_FAITHFULNESS else 0,
        "event": "judged_answer",
        "request_id": request_id,
        "reason": scores["reason"],
    }


def build_report(day, graded, errors, total_samples):
    def avg(field):
        return round(sum(g[field] for g in graded) / len(graded), 2) if graded else None

    summary = {
        "date": day.isoformat(),
        "samples": total_samples,
        "graded": len(graded),
        "judge_errors": errors,
        "avg_faithfulness": avg("faithfulness"),
        "avg_relevance": avg("relevance"),
        "low_faithfulness": sum(g["faithfulness"] <= LOW_FAITHFULNESS for g in graded),
    }
    worst = sorted(graded, key=lambda g: (g["faithfulness"], g["relevance"]))[:20]
    return {"summary": summary, "lowest_scoring": worst}
