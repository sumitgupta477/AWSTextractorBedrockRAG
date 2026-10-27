"""Evaluate the deployed RAG endpoint against a set of test questions.

For each question in a JSONL file, calls POST /query and scores:
  - keyword recall: share of expected_keywords found in the answer
  - citation hit:   whether any citation points at expected_source
  - abstention:     for expect_no_answer questions, whether the model declined
                    instead of inventing an answer
  - latency:        seconds per request

Usage:
  export RAG_API_URL=$(terraform output -raw api_gateway_endpoint)
  export RAG_API_KEY=$(terraform output -raw api_key)
  python eval/run_eval.py eval/questions.jsonl --out eval/results.json

Uses only the Python standard library.
"""

import argparse
import json
import os
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
    args = parser.parse_args()

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

    summary = {
        "total": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 3) if results else 0,
        "avg_keyword_recall": round(statistics.mean(r["keyword_recall"] for r in answered), 3) if answered else None,
        "citation_hit_rate": round(sum(r["citation_hit"] for r in cited) / len(cited), 3) if cited else None,
        "abstention_rate": round(sum(r["abstained"] for r in abstain) / len(abstain), 3) if abstain else None,
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
