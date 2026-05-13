#!/usr/bin/env python3
"""Evaluate an agent output JSONL against DataAgentBench validators.

Usage:
    python mxscripts/eval_output.py stockindex

Reads:  mxdatasets/<benchmark>_output.jsonl
Uses:   query_<benchmark>/query{N}/validate.py  (one per line)

Each output line must have:
    {"input": {"user_message": "..."}, "log": [...], "duration_ms": ...}

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


def evaluate_one(benchmark: str, repo_root: Path, all_results: list) -> tuple[int, int, int] | None:
    """Returns (passed, failed, errors) or None if the benchmark was skipped.
    Appends per-query result dicts to all_results for the combined output file."""
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
    processed_lines = []

    for line in lines:
        query_id = line.get("input", {}).get("query_id")
        if not query_id:
            print(f"  [???] SKIP - no query_id in output line", file=sys.stderr)
            line["eval"] = {"pass": False, "reason": "no query_id in output"}
            processed_lines.append(line)
            continue

        qdir = bench_dir / query_id
        validate_py = qdir / "validate.py"

        if not validate_py.exists():
            print(f"  [{query_id}] SKIP - no validate.py")
            line["eval"] = {"pass": False, "reason": "no validate.py"}
            processed_lines.append(line)
            continue

        answer = extract_answer(line.get("log", []))
        if answer is None:
            print(f"  [{query_id}] ERROR - no answer found in log")
            errors += 1
            line["eval"] = {"pass": False, "reason": "no answer in log"}
            processed_lines.append(line)
            continue

        try:
            validator = load_validator(validate_py, repo_root)
            is_valid, reason = validator(answer)
        except Exception as e:
            print(f"  [{query_id}] ERROR - validator raised: {e}")
            errors += 1
            line["eval"] = {"pass": False, "reason": str(e)}
            processed_lines.append(line)
            continue

        status = "PASS" if is_valid else "FAIL"
        if is_valid:
            passed += 1
        else:
            failed += 1

        print(f"  [{query_id}] {status} - {reason}")
        line["eval"] = {"pass": is_valid, "reason": reason}
        processed_lines.append(line)

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
