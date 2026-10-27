"""Evaluate the deployed RAG endpoint against a set of test questions.

For each question in a JSONL file, calls POST /query and scores:
  - keyword recall: share of expected_keywords found in the answer
  - citation hit:   whether any citation points at expected_source
  - abstention:     for expect_no_answer questions, whether the model declined
                    instead of inventing an answer
  - latency:        seconds per request
  - faithfulness /  (optional, --judge-model) an LLM judge rates 1-5 whether the
    relevance         answer is supported by the retrieved passages and whether
                    it actually answers the question

Usage:
  export RAG_API_URL=$(terraform output -raw api_gateway_endpoint)
  export RAG_API_KEY=$(terraform output -raw api_key)
  python eval/run_eval.py eval/questions.jsonl --out eval/results.json

  # Add LLM-as-judge scores (needs boto3 and AWS credentials with bedrock:InvokeModel)
  python eval/run_eval.py eval/questions.jsonl \
      --judge-model us.anthropic.claude-sonnet-4-5-20250929-v1:0

Without --judge-model only the Python standard library is needed.
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

NO_ANSWER_PHRASES = (
    "unable to assist",
    "i don't know",
    "i do not know",
    "cannot find",
    "could not find",
    "no information",
    "not mentioned",
    "not provided",
    "sorry",
)


def ask(url, api_key, question, timeout=60):
    req = urllib.request.Request(
        url,
        data=json.dumps({"query": question}).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": api_key},
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body, time.perf_counter() - start


def cited_sources(citations):
    """Collect every S3 URI / metadata source referenced by the citations."""
    sources = set()
    for citation in citations or []:
        for ref in citation.get("retrievedReferences", []):
            uri = ref.get("location", {}).get("s3Location", {}).get("uri")
            if uri:
                sources.add(uri)
            source_doc = ref.get("metadata", {}).get("source_document")
            if source_doc:
                sources.add(source_doc)
    return sources


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

Respond with only JSON: {{"faithfulness": <1-5>, "relevance": <1-5>, "reason": "<one sentence>"}}"""


def retrieved_passages(citations, limit=8, max_chars=1500):
    passages = []
    for citation in citations or []:
        for ref in citation.get("retrievedReferences", []):
            text = ref.get("content", {}).get("text")
            if text and text not in passages:
                passages.append(text[:max_chars])
    return passages[:limit]


class Judge:
    """Scores answers with a Bedrock model through the Converse API."""

    def __init__(self, model_id, region=None):
        import boto3  # imported lazily so the basic eval needs no dependencies

        self.model_id = model_id
        self.client = boto3.client("bedrock-runtime", region_name=region)

    def __call__(self, question, answer, citations):
        passages = retrieved_passages(citations)
        prompt = JUDGE_PROMPT.format(
            question=question,
            passages="\n---\n".join(passages) if passages else "(none returned)",
            answer=answer,
        )
        response = self.client.converse(
            modelId=self.model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 300, "temperature": 0},
        )
        text = response["output"]["message"]["content"][0]["text"]
        return parse_judgement(text)


def parse_judgement(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"judge_error": "no JSON in judge response"}
    try:
        data = json.loads(match.group(0))
        return {
            "faithfulness": int(data["faithfulness"]),
            "relevance": int(data["relevance"]),
            "judge_reason": str(data.get("reason", "")),
        }
    except (ValueError, KeyError, TypeError) as e:
        return {"judge_error": repr(e)}


def score(case, answer, citations):
    text = answer.lower()
    result = {}

    if case.get("expect_no_answer"):
        result["abstained"] = any(p in text for p in NO_ANSWER_PHRASES)
        result["passed"] = result["abstained"]
        return result

    keywords = case.get("expected_keywords", [])
    found = [k for k in keywords if k.lower() in text]
    result["keyword_recall"] = len(found) / len(keywords) if keywords else 1.0
    result["missing_keywords"] = [k for k in keywords if k not in found]

    expected_source = case.get("expected_source")
    if expected_source:
        result["citation_hit"] = any(expected_source in s for s in cited_sources(citations))
    else:
        result["citation_hit"] = None

    result["passed"] = result["keyword_recall"] >= 0.5 and result["citation_hit"] is not False
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("questions", help="JSONL file of test cases")
    parser.add_argument("--url", default=os.environ.get("RAG_API_URL"))
    parser.add_argument("--api-key", default=os.environ.get("RAG_API_KEY"))
    parser.add_argument("--out", help="Write detailed results to this JSON file")
    parser.add_argument("--judge-model", help="Bedrock model ID or inference profile for LLM-as-judge scoring")
    parser.add_argument("--judge-region", default=os.environ.get("AWS_REGION"), help="Region for the judge model")
    parser.add_argument("--min-faithfulness", type=float, default=4.0,
                        help="With --judge-model, answered cases need at least this faithfulness score to pass")
    args = parser.parse_args()

    judge = Judge(args.judge_model, args.judge_region) if args.judge_model else None

    if not args.url or not args.api_key:
        sys.exit("Set RAG_API_URL and RAG_API_KEY (or pass --url / --api-key).")

    with open(args.questions, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]

    results, latencies = [], []
    for i, case in enumerate(cases, 1):
        try:
            body, latency = ask(args.url, args.api_key, case["question"])
            latencies.append(latency)
            scored = score(case, body.get("answer", ""), body.get("citations"))
            if judge and not case.get("expect_no_answer"):
                try:
                    scored.update(judge(case["question"], body.get("answer", ""), body.get("citations")))
                except Exception as e:  # judge problems shouldn't abort the whole run
                    scored["judge_error"] = repr(e)
                if "faithfulness" in scored and scored["faithfulness"] < args.min_faithfulness:
                    scored["passed"] = False
            scored.update(answer=body.get("answer", ""), latency_s=round(latency, 2))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            scored = {"passed": False, "error": repr(e)}
        scored["question"] = case["question"]
        results.append(scored)
        mark = "PASS" if scored["passed"] else "FAIL"
        print(f"[{i}/{len(cases)}] {mark}  {case['question'][:70]}")

    passed = sum(r["passed"] for r in results)
    answered = [r for r in results if "keyword_recall" in r]
    cited = [r for r in answered if r.get("citation_hit") is not None]
    abstain = [r for r in results if "abstained" in r]
    judged = [r for r in results if "faithfulness" in r]

    summary = {
        "total": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 3) if results else 0,
        "avg_keyword_recall": round(statistics.mean(r["keyword_recall"] for r in answered), 3) if answered else None,
        "citation_hit_rate": round(sum(r["citation_hit"] for r in cited) / len(cited), 3) if cited else None,
        "abstention_rate": round(sum(r["abstained"] for r in abstain) / len(abstain), 3) if abstain else None,
        "avg_faithfulness": round(statistics.mean(r["faithfulness"] for r in judged), 2) if judged else None,
        "avg_relevance": round(statistics.mean(r["relevance"] for r in judged), 2) if judged else None,
        "latency_p50_s": round(statistics.median(latencies), 2) if latencies else None,
        "latency_max_s": round(max(latencies), 2) if latencies else None,
    }

    print("\nSummary")
    for key, value in summary.items():
        print(f"  {key:20} {value}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "results": results}, f, indent=2)
        print(f"\nDetailed results written to {args.out}")

    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
