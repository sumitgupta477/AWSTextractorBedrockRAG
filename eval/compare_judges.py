"""Compare candidate LLM judges against your own hand-graded answers.

A judge is only useful if it agrees with you. This script runs several
Bedrock models over a "gold" file you graded yourself and reports, per model:

  - agreement:   share of scores within 1 point of yours (and exact matches)
  - MAE:         mean absolute error versus your scores
  - catch rate:  of the answers you marked as bad (hallucinated or
                 faithfulness <= 2), how many the judge flagged
  - false alarms: of the answers you rated good (faithfulness >= 4),
                 how many the judge wrongly flagged
  - consistency: with --repeats 2+, share of answers scored identically each run
  - tokens and (with --price) cost per graded answer

It uses the exact prompt and parser of the nightly judge Lambda, so the
results carry over to production.

Typical workflow:
  # 1. Turn some nightly samples into a gold file to grade by hand
  aws s3 sync s3://<eval-samples-bucket>/samples/2026/10/26/ ./samples
  python eval/compare_judges.py --make-gold ./samples > eval/judge_gold.jsonl
  #    ...fill in human_faithfulness / human_relevance (1-5) and hallucinated (true/false)

  # 2. Compare judges
  python eval/compare_judges.py eval/judge_gold.jsonl \\
      --models us.anthropic.claude-sonnet-4-5-20250929-v1:0,amazon.nova-pro-v1:0,amazon.nova-lite-v1:0 \\
      --price amazon.nova-pro-v1:0=0.0008,0.0032 --price amazon.nova-lite-v1:0=0.00006,0.00024 \\
      --repeats 2 --out eval/judge_comparison.json

Prices are USD per 1,000 input and output tokens; check the Bedrock pricing page.
Needs boto3 and AWS credentials allowed to call the candidate models.
"""

import argparse
import importlib.util
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BAD_FAITHFULNESS = 2   # your score at or below this = a bad answer the judge should catch
GOOD_FAITHFULNESS = 4  # your score at or above this = a good answer the judge shouldn't flag


def load_judge_module(region=None):
    """Import the nightly judge Lambda to reuse its prompt and parser verbatim."""
    # The module creates AWS clients at import time; give them a region so that works offline too.
    os.environ.setdefault("AWS_DEFAULT_REGION", region or "us-east-1")
    os.environ.setdefault("EVAL_SAMPLE_BUCKET", "unused")
    os.environ.setdefault("JUDGE_MODEL_ID", "unused")
    spec = importlib.util.spec_from_file_location("nightly_judge", ROOT / "lambda/judge/main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_gold(samples_dir):
    """Print gold-file rows (with blank human scores) from downloaded nightly samples."""
    for path in sorted(Path(samples_dir).rglob("*.json")):
        sample = json.loads(path.read_text(encoding="utf-8"))
        print(json.dumps({
            "id": sample.get("request_id") or path.stem,
            "question": sample.get("question", ""),
            "answer": sample.get("answer", ""),
            "passages": sample.get("passages", []),
            "human_faithfulness": None,
            "human_relevance": None,
            "hallucinated": None,
        }))


def load_gold(path):
    rows, skipped = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("human_faithfulness") is None or row.get("human_relevance") is None:
                skipped += 1
                continue
            rows.append(row)
    if skipped:
        print(f"Skipping {skipped} row(s) without human scores.", file=sys.stderr)
    return rows


def run_judge(client, judge_module, model_id, row, max_passages=8, max_chars=1500):
    passages = [p[:max_chars] for p in row.get("passages", [])[:max_passages]]
    prompt = judge_module.JUDGE_PROMPT.format(
        question=row.get("question", ""),
        passages="\n---\n".join(passages) if passages else "(none retrieved)",
        answer=row.get("answer", ""),
    )
    started = time.perf_counter()
    response = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 300, "temperature": 0},
    )
    scores = judge_module.parse_judgement(response["output"]["message"]["content"][0]["text"])
    usage = response.get("usage", {})
    scores["input_tokens"] = usage.get("inputTokens", 0)
    scores["output_tokens"] = usage.get("outputTokens", 0)
    scores["latency_s"] = time.perf_counter() - started
    return scores


def is_bad(row):
    return bool(row.get("hallucinated")) or row["human_faithfulness"] <= BAD_FAITHFULNESS


def evaluate_model(client, judge_module, model_id, gold, repeats=1, price=None):
    runs = []  # runs[r][i] = scores dict or None
    errors = 0
    for _ in range(repeats):
        scores = []
        for row in gold:
            try:
                scores.append(run_judge(client, judge_module, model_id, row))
            except Exception as e:
                errors += 1
                scores.append(None)
                print(f"  {model_id}: error on {row.get('id', '?')}: {e!r}", file=sys.stderr)
        runs.append(scores)

    first = runs[0]
    pairs = [(row, s) for row, s in zip(gold, first) if s]
    low = judge_module.LOW_FAITHFULNESS

    def within1(field, human_field):
        return sum(abs(s[field] - row[human_field]) <= 1 for row, s in pairs) / len(pairs) if pairs else 0

    def exact(field, human_field):
        return sum(s[field] == row[human_field] for row, s in pairs) / len(pairs) if pairs else 0

    def mae(field, human_field):
        return statistics.mean(abs(s[field] - row[human_field]) for row, s in pairs) if pairs else None

    bad = [(row, s) for row, s in pairs if is_bad(row)]
    good = [(row, s) for row, s in pairs if row["human_faithfulness"] >= GOOD_FAITHFULNESS]

    consistency = None
    if repeats > 1:
        stable = sum(
            all(run[i] and run[i]["faithfulness"] == first[i]["faithfulness"]
                and run[i]["relevance"] == first[i]["relevance"] for run in runs)
            for i in range(len(gold)) if first[i]
        )
        consistency = stable / len(pairs) if pairs else None

    in_tokens = statistics.mean(s["input_tokens"] for _, s in pairs) if pairs else 0
    out_tokens = statistics.mean(s["output_tokens"] for _, s in pairs) if pairs else 0
    cost = None
    if price:
        cost = in_tokens / 1000 * price[0] + out_tokens / 1000 * price[1]

    return {
        "model": model_id,
        "graded": len(pairs),
        "errors": errors,
        "faithfulness_within1": round(within1("faithfulness", "human_faithfulness"), 3),
        "faithfulness_exact": round(exact("faithfulness", "human_faithfulness"), 3),
        "faithfulness_mae": round(mae("faithfulness", "human_faithfulness"), 2) if pairs else None,
        "relevance_within1": round(within1("relevance", "human_relevance"), 3),
        "relevance_mae": round(mae("relevance", "human_relevance"), 2) if pairs else None,
        "bad_answers": len(bad),
        "catch_rate": round(sum(s["faithfulness"] <= low for _, s in bad) / len(bad), 3) if bad else None,
        "false_alarm_rate": round(sum(s["faithfulness"] <= low for _, s in good) / len(good), 3) if good else None,
        "consistency": round(consistency, 3) if consistency is not None else None,
        "avg_input_tokens": round(in_tokens),
        "avg_output_tokens": round(out_tokens),
        "cost_per_answer_usd": round(cost, 6) if cost is not None else None,
        "avg_latency_s": round(statistics.mean(s["latency_s"] for _, s in pairs), 2) if pairs else None,
    }


def passes(result, min_agreement, min_catch):
    if result["graded"] == 0:
        return False
    if result["faithfulness_within1"] < min_agreement:
        return False
    if result["catch_rate"] is not None and result["catch_rate"] < min_catch:
        return False
    return True


def parse_prices(values):
    prices = {}
    for value in values or []:
        model, _, rates = value.partition("=")
        in_rate, _, out_rate = rates.partition(",")
        prices[model] = (float(in_rate), float(out_rate))
    return prices


def print_table(results, min_agreement, min_catch):
    def fmt(v, pct=False):
        if v is None:
            return "-"
        return f"{v:.0%}" if pct else str(v)

    headers = ["model", "agree±1", "exact", "MAE", "catch", "false alarm", "consistent", "$/answer", "pass"]
    rows = [[
        r["model"],
        fmt(r["faithfulness_within1"], True),
        fmt(r["faithfulness_exact"], True),
        fmt(r["faithfulness_mae"]),
        fmt(r["catch_rate"], True),
        fmt(r["false_alarm_rate"], True),
        fmt(r["consistency"], True),
        fmt(r["cost_per_answer_usd"]),
        "yes" if passes(r, min_agreement, min_catch) else "no",
    ] for r in results]
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    for line in [headers, ["-" * w for w in widths], *rows]:
        print("  ".join(str(x).ljust(w) for x, w in zip(line, widths)))


def recommend(results, min_agreement, min_catch):
    passing = [r for r in results if passes(r, min_agreement, min_catch)]
    if not passing:
        return None, "No judge met the bar. Try a stronger model, or check your gold grades for consistency."
    priced = [r for r in passing if r["cost_per_answer_usd"] is not None]
    if priced:
        best = min(priced, key=lambda r: r["cost_per_answer_usd"])
        return best["model"], f"Cheapest judge that meets the bar: {best['model']}"
    best = max(passing, key=lambda r: (r["catch_rate"] or 0, r["faithfulness_within1"]))
    return best["model"], (f"Best-agreeing judge that meets the bar: {best['model']} "
                           "(pass --price to pick by cost instead)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("gold", nargs="?", help="JSONL of hand-graded answers")
    parser.add_argument("--models", help="Comma-separated Bedrock model or inference-profile IDs")
    parser.add_argument("--repeats", type=int, default=1, help="Runs per model, to measure consistency")
    parser.add_argument("--price", action="append", metavar="MODEL=IN,OUT",
                        help="USD per 1K input,output tokens for a model (repeatable)")
    parser.add_argument("--min-agreement", type=float, default=0.8, help="Required faithfulness agreement within 1 point")
    parser.add_argument("--min-catch", type=float, default=0.8, help="Required share of bad answers caught")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION"))
    parser.add_argument("--out", help="Write full results to this JSON file")
    parser.add_argument("--make-gold", metavar="SAMPLES_DIR",
                        help="Print a gold-file template from downloaded nightly samples and exit")
    args = parser.parse_args()

    if args.make_gold:
        make_gold(args.make_gold)
        return
    if not args.gold or not args.models:
        parser.error("gold file and --models are required (or use --make-gold)")

    gold = load_gold(args.gold)
    if not gold:
        sys.exit("No hand-graded rows found. Fill in human_faithfulness and human_relevance first.")
    if len(gold) < 20:
        print(f"Note: only {len(gold)} graded answers; 30-50 gives more reliable comparisons.", file=sys.stderr)

    import boto3

    client = boto3.client("bedrock-runtime", region_name=args.region)
    judge_module = load_judge_module(args.region)
    prices = parse_prices(args.price)
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    results = []
    for model in models:
        print(f"Judging {len(gold)} answers with {model} ...", file=sys.stderr)
        results.append(evaluate_model(client, judge_module, model, gold, args.repeats, prices.get(model)))

    print()
    print_table(results, args.min_agreement, args.min_catch)
    choice, message = recommend(results, args.min_agreement, args.min_catch)
    print(f"\n{message}")
    if choice:
        print(f"Deploy with: -var judge_model_id={choice}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"thresholds": {"min_agreement": args.min_agreement, "min_catch": args.min_catch},
                       "results": results, "recommended": choice}, f, indent=2)
        print(f"Full results written to {args.out}")


if __name__ == "__main__":
    main()
