#!/usr/bin/env python3
"""Compare a promptfoo eval run against a saved baseline to detect drift.

Usage:
  python evals/drift_check.py evals/results/latest.json                   # compare
  python evals/drift_check.py evals/results/latest.json --update-baseline # save new baseline

What it checks:
  * Quality metrics (promptfoo `metric:` names, scored 0-1): fails if any metric
    drops by more than --tolerance (absolute) versus the baseline.
  * Overall pass rate: same rule.
  * Error rate (provider/API failures): fails if it rises by more than --tolerance.
  * Per-test regressions: tests whose pass rate fell by 0.5 or more are listed.
  * Latency and token usage: warnings only (they are noisy across runs and
    machines), unless --fail-on-cost is set.
  * Fingerprint: warns if the model, grader or promptfoo version differs from
    the baseline, because then a score change may not be the app's fault.

Metrics are compared only over tests present in both runs, so adding a test
does not count as drift. The script uses only the Python standard library.
Exit code: 0 = no drift, 1 = drift detected, 2 = bad input.
"""

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

SCHEMA_VERSION = 1


def load_results(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    try:
        rows = data["results"]["results"]
    except (KeyError, TypeError):
        sys.exit(f"error: {path} does not look like promptfoo output (run `promptfoo eval -o file.json`)")
    if not rows:
        sys.exit(f"error: {path} contains no results")
    return data, rows


def test_key(row):
    tc = row.get("testCase") or {}
    if tc.get("description"):
        return tc["description"]
    # Fall back to a stable hash of the test's variables.
    return "vars:" + hashlib.sha1(json.dumps(row.get("vars", {}), sort_keys=True).encode()).hexdigest()[:12]


def is_error(row):
    # promptfoo marks provider/runtime failures with failureReason 2 (ERROR)
    # and leaves an `error` field; assertion failures are reason 1.
    return row.get("failureReason") == 2 or (bool(row.get("error")) and not row.get("namedScores"))


def summarize(data, rows):
    """Aggregate a run into per-test and overall numbers."""
    per_test = defaultdict(lambda: {"runs": 0, "passes": 0, "errors": 0, "metrics": defaultdict(list)})
    latencies, tokens = [], []

    for row in rows:
        t = per_test[test_key(row)]
        t["runs"] += 1
        if is_error(row):
            t["errors"] += 1
            continue
        if row.get("success"):
            t["passes"] += 1
        for metric, score in (row.get("namedScores") or {}).items():
            if isinstance(score, (int, float)):
                t["metrics"][metric].append(float(score))
        if isinstance(row.get("latencyMs"), (int, float)):
            latencies.append(row["latencyMs"])
        total = ((row.get("response") or {}).get("tokenUsage") or {}).get("total")
        if isinstance(total, (int, float)):
            tokens.append(total)

    tests = {}
    for key, t in per_test.items():
        tests[key] = {
            "runs": t["runs"],
            "pass_rate": t["passes"] / t["runs"],
            "error_rate": t["errors"] / t["runs"],
            "metrics": {m: sum(v) / len(v) for m, v in t["metrics"].items() if v},
        }

    providers = sorted({(r.get("provider") or {}).get("id", "unknown") for r in rows})
    grader = (((data.get("config") or {}).get("defaultTest") or {}).get("options") or {}).get("provider")
    if isinstance(grader, dict):
        grader = grader.get("id")
    meta = data.get("metadata") or {}

    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fingerprint": {
            "providers": providers,
            "grader": grader,
            "promptfoo_version": meta.get("promptfooVersion"),
        },
        "num_results": len(rows),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "avg_tokens": round(sum(tokens) / len(tokens), 1) if tokens else None,
        "tests": tests,
    }


def aggregate(tests, keys):
    """Mean of each metric over the given tests (each test weighted equally)."""
    sums = defaultdict(list)
    for k in keys:
        t = tests[k]
        sums["pass_rate"].append(t["pass_rate"])
        sums["error_rate"].append(t["error_rate"])
        for m, v in t["metrics"].items():
            sums[m].append(v)
    return {m: sum(v) / len(v) for m, v in sums.items() if v}


def compare(baseline, current, tolerance, latency_tolerance, token_tolerance, fail_on_cost):
    lines, warnings = [], []
    drift = False

    shared = sorted(set(baseline["tests"]) & set(current["tests"]))
    added = sorted(set(current["tests"]) - set(baseline["tests"]))
    removed = sorted(set(baseline["tests"]) - set(current["tests"]))
    if not shared:
        sys.exit("error: no tests in common with the baseline; re-baseline with --update-baseline")

    base = aggregate(baseline["tests"], shared)
    cur = aggregate(current["tests"], shared)

    lines.append(f"Compared {len(shared)} tests present in both runs "
                 f"(baseline from {baseline.get('created_at', 'unknown date')}).\n")
    lines.append("| Metric | Baseline | Current | Change | Status |")
    lines.append("|---|---:|---:|---:|---|")
    for metric in sorted(set(base) | set(cur), key=lambda m: (m == "error_rate", m)):
        b, c = base.get(metric), cur.get(metric)
        if b is None or c is None:
            lines.append(f"| {metric} | {fmt(b)} | {fmt(c)} | n/a | new or missing metric |")
            continue
        delta = c - b
        worse = delta > tolerance if metric == "error_rate" else delta < -tolerance
        drift |= worse
        lines.append(f"| {metric} | {b:.3f} | {c:.3f} | {delta:+.3f} | {'DRIFT' if worse else 'ok'} |")

    for label, key, tol in (("Avg latency (ms)", "avg_latency_ms", latency_tolerance),
                            ("Avg tokens per call", "avg_tokens", token_tolerance)):
        b, c = baseline.get(key), current.get(key)
        if b and c:
            change = (c - b) / b
            over = change > tol
            if over:
                warnings.append(f"{label} rose {change:+.0%} ({b} -> {c}), above the {tol:.0%} limit.")
                drift |= fail_on_cost
            lines.append(f"| {label} | {b} | {c} | {change:+.0%} | {'WARN' if over else 'ok'} |")

    regressions, improvements = [], []
    for k in shared:
        d = current["tests"][k]["pass_rate"] - baseline["tests"][k]["pass_rate"]
        if d <= -0.5:
            regressions.append(f"- `{k}`: pass rate {baseline['tests'][k]['pass_rate']:.0%} -> "
                               f"{current['tests'][k]['pass_rate']:.0%}")
        elif d >= 0.5:
            improvements.append(f"- `{k}`")
    if regressions:
        lines.append("\n**Tests that regressed:**")
        lines.extend(regressions)
    if improvements:
        lines.append("\n**Tests that improved:**")
        lines.extend(improvements)
    if added:
        warnings.append(f"{len(added)} new test(s) not in the baseline (ignored in the comparison): "
                        + ", ".join(f"`{a}`" for a in added[:5]) + (" ..." if len(added) > 5 else ""))
    if removed:
        warnings.append(f"{len(removed)} baseline test(s) missing from this run.")

    bf, cf = baseline.get("fingerprint", {}), current.get("fingerprint", {})
    for field in ("providers", "grader", "promptfoo_version"):
        if bf.get(field) != cf.get(field):
            warnings.append(f"{field} changed from `{bf.get(field)}` to `{cf.get(field)}`; "
                            "score changes may come from that rather than from the app. "
                            "Re-baseline once you accept the new setup.")
    return drift, lines, warnings


def fmt(v):
    return "-" if v is None else f"{v:.3f}"


def write_summary(text):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("results", help="promptfoo JSON output (promptfoo eval -o results.json)")
    p.add_argument("--baseline", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline.json"))
    p.add_argument("--update-baseline", action="store_true", help="save this run as the new baseline")
    p.add_argument("--tolerance", type=float, default=0.05,
                   help="max allowed absolute drop in a 0-1 metric (default 0.05)")
    p.add_argument("--latency-tolerance", type=float, default=0.5,
                   help="relative latency increase that triggers a warning (default 0.5 = +50%%)")
    p.add_argument("--token-tolerance", type=float, default=0.3,
                   help="relative token-usage increase that triggers a warning (default 0.3 = +30%%)")
    p.add_argument("--fail-on-cost", action="store_true", help="treat latency/token warnings as drift")
    p.add_argument("--require-baseline", action="store_true", help="fail if no baseline exists")
    args = p.parse_args()

    data, rows = load_results(args.results)
    current = summarize(data, rows)

    if args.update_baseline:
        with open(args.baseline, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, sort_keys=True)
            f.write("\n")
        overall = aggregate(current["tests"], current["tests"].keys())
        print(f"Baseline saved to {args.baseline} ({len(current['tests'])} tests).")
        for m, v in sorted(overall.items()):
            print(f"  {m}: {v:.3f}")
        return 0

    if not os.path.exists(args.baseline):
        msg = (f"No baseline found at {args.baseline}. Run the eval once against a known-good "
               "version and save it with --update-baseline, then commit baseline.json.")
        print(("error: " if args.require_baseline else "warning: ") + msg)
        write_summary(f"## LLM eval drift check\n\n:warning: {msg}")
        return 1 if args.require_baseline else 0

    with open(args.baseline, encoding="utf-8") as f:
        baseline = json.load(f)

    drift, lines, warnings = compare(baseline, current, args.tolerance, args.latency_tolerance,
                                     args.token_tolerance, args.fail_on_cost)
    title = "## LLM eval drift check: " + ("DRIFT DETECTED" if drift else "no drift")
    body = "\n".join([title, ""] + lines)
    if warnings:
        body += "\n\n**Warnings:**\n" + "\n".join(f"- {w}" for w in warnings)
    print(body)
    write_summary(body)
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
