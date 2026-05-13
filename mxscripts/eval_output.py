#!/usr/bin/env python3
"""Evaluate an agent output JSONL against DataAgentBench validators.

Usage:
    python mxscripts/eval_output.py stockindex

Reads:  mxdatasets/<benchmark>_output.jsonl
Uses:   query_<benchmark>/query{N}/validate.py  (one per line)

Each output line must have one of:
    {"input": {...}, "log":  [...],   "duration_ms": ...}    # single-run
    {"input": {...}, "logs": [[...]], "duration_ms": ...}    # multi-run

For multi-run rows (`logs` is an array of conversation logs, produced by
the bench runner with TIMES_RUN/DAB_TIMES_RUN > 1) we evaluate each run
and collapse to **one row per query_id**:
  - if every run passes -> emit one row carrying the first log as `log`
  - if any run fails    -> emit one row carrying the first failing run's
                           log as `log` (its verdict becomes the row's
                           verdict). Subsequent runs — pass or fail —
                           are not surfaced here; the raw `_output.jsonl`
                           still has every log if you need to inspect
                           flakiness.

Keeping one row per query_id means the overall pass-rate calculation
matches the single-run case (rows in == queries; passed/total is honest).

Each emitted evalrun row always has singular `log` (never `logs`), so the
output remains importable by the /benchmark viewer downstream.

The final answer is extracted from the last assistant message in the log.
"""

import argparse
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path


def extract_answer(log: list) -> str | None:
    """Extract the final answer from the log.

    Supports two log formats:
    - New format: entries with _type="task_result" and result.content_blocks
    - Legacy format: entries with role="assistant" and content list/string
    """
    # New format: look for last task_result with content_blocks
    for entry in reversed(log):
        if entry.get("_type") == "task_result" and "result" in entry:
            result = entry["result"]
            if isinstance(result, str):
                result = json.loads(result)
            if "content_blocks" in result:
                text_parts = [
                    b.get("text", "")
                    for b in result["content_blocks"]
                    if b.get("type") == "text"
                ]
                text = "\n".join(text_parts).strip()
                if text:
                    return text

    # Legacy format: last assistant message
    for entry in reversed(log):
        if entry.get("role") != "assistant":
            continue
        content = entry.get("content", "")
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            text = "\n".join(text_parts)
        else:
            text = str(content)

        if not text.strip():
            continue

        return text.strip()

    return None


def load_validator(validate_py: Path, repo_root: Path):
    """Dynamically load a validate.py and return its validate function."""
    root_str = str(repo_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    spec = importlib.util.spec_from_file_location("validator", str(validate_py))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.validate


def evaluate_log(validate_py: Path, repo_root: Path, log: list) -> tuple[str, str]:
    """Evaluate a single conversation log.

    Returns (outcome, reason) where outcome is one of: 'pass', 'fail', 'error'.
    """
    answer = extract_answer(log)
    if answer is None:
        return ("error", "no answer in log")
    try:
        validator = load_validator(validate_py, repo_root)
        is_valid, reason = validator(answer)
    except Exception as e:
        return ("error", str(e))
    return ("pass" if is_valid else "fail", reason)


def _emit(
    line: dict,
    log: list,
    outcome: str,
    reason: str,
    processed_lines: list,
    failure_rate: float,
) -> None:
    """Build one evalrun row from a single conversation log + verdict.

    Always emits singular `log` (never `logs`), so the resulting evalrun
    file is importable by the /benchmark viewer regardless of whether the
    source row was single-run or multi-run.

    `failure_rate` is the percentage of runs that did NOT pass (0..100).
    For single-run rows: 0 on pass, 100 on fail/error. For multi-run rows:
    `(non_pass_runs / total_runs) * 100`. Stored alongside the verdict so
    flakiness is visible without re-evaluating.
    """
    new_line = {**line}
    new_line.pop("logs", None)
    new_line["log"] = log
    new_line["eval"] = {
        "pass": outcome == "pass",
        "reason": reason,
        "failure_rate": failure_rate,
    }
    processed_lines.append(new_line)


def evaluate_one(benchmark: str, repo_root: Path, all_results: list) -> tuple[int, int, int] | None:
    """Returns (passed, failed, errors) counted across emitted evalrun rows,
    or None if the benchmark was skipped. Appends per-query result dicts to
    all_results for the combined output file."""
    output_path = repo_root / "mxdatasets" / f"{benchmark}_output.jsonl"
    bench_dir = repo_root / f"query_{benchmark}"

    if not output_path.exists():
        print(f"[{benchmark}] SKIP - no output file at {output_path}", file=sys.stderr)
        return None

    print(f"\n=== {benchmark} ===")

    with open(output_path) as f:
        lines = [json.loads(line) for line in f if line.strip()]

    passed = 0
    failed = 0
    errors = 0
    processed_lines: list[dict] = []

    for line in lines:
        query_id = line.get("input", {}).get("query_id")
        if not query_id:
            print("  [???] SKIP - no query_id in output line", file=sys.stderr)
            stub_log = line.get("log") or (line.get("logs", [[]])[0] if line.get("logs") else [])
            _emit(line, stub_log, "fail", "no query_id in output", processed_lines, 100.0)
            failed += 1
            continue

        qdir = bench_dir / query_id
        validate_py = qdir / "validate.py"

        if not validate_py.exists():
            print(f"  [{query_id}] SKIP - no validate.py")
            stub_log = line.get("log") or (line.get("logs", [[]])[0] if line.get("logs") else [])
            _emit(line, stub_log, "fail", "no validate.py", processed_lines, 100.0)
            failed += 1
            continue

        # Multi-run row: `logs` is an array of conversation logs, one per
        # repeat. Evaluate each; emit exactly one evalrun row:
        #   - all pass     -> first log, pass
        #   - any non-pass -> first non-pass log (its verdict)
        # One-row-per-query_id keeps the pass-rate honest (denominator =
        # query count, same as single-run). `failure_rate` carries the
        # per-query flakiness (non-pass / total * 100).
        if isinstance(line.get("logs"), list):
            logs: list = line["logs"]
            if not logs:
                print(f"  [{query_id}] ERROR - empty `logs` array")
                _emit(line, [], "error", "empty logs array", processed_lines, 100.0)
                errors += 1
                continue

            evaluations = [
                (evaluate_log(validate_py, repo_root, log), log)
                for log in logs
            ]
            outcomes = [outcome for ((outcome, _), _) in evaluations]
            n_pass = outcomes.count("pass")
            n_fail = outcomes.count("fail")
            n_err = outcomes.count("error")
            n_non_pass = len(logs) - n_pass
            failure_rate = (n_non_pass / len(logs)) * 100.0

            if all(o == "pass" for o in outcomes):
                (_, reason), log = evaluations[0]
                _emit(line, log, "pass", reason, processed_lines, failure_rate)
                passed += 1
                print(f"  [{query_id}] PASS ({n_pass}/{len(logs)} runs, failure_rate={failure_rate:.1f}%) - {reason}")
            else:
                # First non-pass run. Its outcome (fail or error) becomes
                # this query's verdict; later runs are ignored.
                first_bad = next(
                    ((res, log) for (res, log) in evaluations if res[0] != "pass"),
                    None,
                )
                assert first_bad is not None  # not-all-pass guarantees one
                (outcome, reason), log = first_bad
                _emit(line, log, outcome, reason, processed_lines, failure_rate)
                if outcome == "fail":
                    failed += 1
                else:
                    errors += 1
                status = outcome.upper()
                print(f"  [{query_id}] {status} ({n_pass}/{len(logs)} runs passed, fail={n_fail} err={n_err}, failure_rate={failure_rate:.1f}%) - {reason}")
            continue

        # Single-run row (legacy / TIMES_RUN=1): one `log`, one verdict.
        # failure_rate collapses to 0 (pass) or 100 (non-pass).
        log = line.get("log", [])
        outcome, reason = evaluate_log(validate_py, repo_root, log)
        single_failure_rate = 0.0 if outcome == "pass" else 100.0
        _emit(line, log, outcome, reason, processed_lines, single_failure_rate)
        if outcome == "pass":
            passed += 1
        elif outcome == "fail":
            failed += 1
        else:
            errors += 1
        status = {"pass": "PASS", "fail": "FAIL", "error": "ERROR"}[outcome]
        print(f"  [{query_id}] {status} - {reason}")

    for pl in processed_lines:
        all_results.append({"benchmark": benchmark, **pl})

    total = passed + failed + errors
    if total:
        print(f"  -> {passed}/{total} passed ({passed/total*100:.1f}%) | failed={failed} errors={errors}")
    else:
        print("  -> no queries evaluated")
    return (passed, failed, errors)


def main():
    parser = argparse.ArgumentParser(description="Evaluate agent output against DAB validators")
    parser.add_argument("benchmarks", nargs="*", help="Sub-benchmark name(s), e.g. stockindex stockmarket")
    parser.add_argument("--all", action="store_true", help="Evaluate every benchmark with an output file in mxdatasets/")
    parser.add_argument("--file", type=str, default=None, help="Path to write the combined results JSONL (default: mxdatasets/evalrun_<timestamp>.jsonl)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    out_dir = repo_root / "mxdatasets"

    if args.all:
        benchmarks = sorted(
            p.name[:-len("_output.jsonl")]
            for p in out_dir.glob("*_output.jsonl")
            if not p.name.endswith("_output_processed.jsonl")
        )
        if not benchmarks:
            print(f"No *_output.jsonl files found in {out_dir}", file=sys.stderr)
            sys.exit(1)
    else:
        benchmarks = args.benchmarks
        if not benchmarks:
            parser.error("Specify one or more benchmarks, or pass --all")

    totals = {"passed": 0, "failed": 0, "errors": 0}
    per_bench: list[tuple[str, int, int, int]] = []
    all_results: list[dict] = []

    for b in benchmarks:
        result = evaluate_one(b, repo_root, all_results)
        if result is None:
            continue
        p, f_, e = result
        totals["passed"] += p
        totals["failed"] += f_
        totals["errors"] += e
        per_bench.append((b, p, f_, e))

    if len(per_bench) > 1:
        print(f"\n{'='*60}")
        print(f"{'benchmark':<24}{'pass':>6}{'fail':>6}{'err':>6}{'total':>7}{'rate':>8}")
        print("-" * 60)
        for b, p, f_, e in per_bench:
            t = p + f_ + e
            rate = f"{p/t*100:.1f}%" if t else "-"
            print(f"{b:<24}{p:>6}{f_:>6}{e:>6}{t:>7}{rate:>8}")
        grand_total = totals["passed"] + totals["failed"] + totals["errors"]
        rate = f"{totals['passed']/grand_total*100:.1f}%" if grand_total else "-"
        print("-" * 60)
        print(f"{'TOTAL':<24}{totals['passed']:>6}{totals['failed']:>6}{totals['errors']:>6}{grand_total:>7}{rate:>8}")

    if all_results:
        if args.file:
            evalrun_path = Path(args.file).expanduser().resolve()
            evalrun_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            evalrun_path = out_dir / f"evalrun_{timestamp}.jsonl"
        with open(evalrun_path, "w") as f:
            for r in all_results:
                f.write(json.dumps(r) + "\n")
        print(f"\nResults saved to {evalrun_path}")


if __name__ == "__main__":
    main()
