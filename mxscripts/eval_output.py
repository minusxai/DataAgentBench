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
from pathlib import Path


def extract_answer(log: list) -> str | None:
    """Extract the final answer from the last assistant message in the log."""
    for entry in reversed(log):
        if entry.get("role") != "assistant":
            continue
        content = entry.get("content", "")
        # content can be a string or a list of parts
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            text = "\n".join(text_parts)
        else:
            text = str(content)

        if not text.strip():
            continue

        return text.strip()

    return None


def load_validator(validate_py: Path):
    """Dynamically load a validate.py and return its validate function."""
    spec = importlib.util.spec_from_file_location("validator", str(validate_py))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.validate


def main():
    parser = argparse.ArgumentParser(description="Evaluate agent output against DAB validators")
    parser.add_argument("benchmark", help="Sub-benchmark name, e.g. stockindex")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    output_path = repo_root / "mxdatasets" / f"{args.benchmark}_output.jsonl"
    bench_dir = repo_root / f"query_{args.benchmark}"

    if not output_path.exists():
        print(f"Error: output file not found: {output_path}", file=sys.stderr)
        sys.exit(1)

    # Load all output lines
    with open(output_path) as f:
        lines = [json.loads(line) for line in f if line.strip()]

    # Match lines to query dirs (query1, query2, ...)
    query_dirs = sorted(
        [d for d in bench_dir.iterdir() if d.is_dir() and d.name.startswith("query") and d.name != "query_dataset"],
        key=lambda d: int("".join(filter(str.isdigit, d.name)) or "0"),
    )

    if len(lines) != len(query_dirs):
        print(f"Warning: {len(lines)} output lines but {len(query_dirs)} queries", file=sys.stderr)

    passed = 0
    failed = 0
    errors = 0
    processed_lines = []

    for i, (line, qdir) in enumerate(zip(lines, query_dirs)):
        query_name = qdir.name
        validate_py = qdir / "validate.py"

        if not validate_py.exists():
            print(f"  [{query_name}] SKIP - no validate.py")
            line["eval"] = {"pass": False, "reason": "no validate.py"}
            processed_lines.append(line)
            continue

        answer = extract_answer(line.get("log", []))
        if answer is None:
            print(f"  [{query_name}] ERROR - no answer found in log")
            errors += 1
            line["eval"] = {"pass": False, "reason": "no answer in log"}
            processed_lines.append(line)
            continue

        try:
            validator = load_validator(validate_py)
            is_valid, reason = validator(answer)
        except Exception as e:
            print(f"  [{query_name}] ERROR - validator raised: {e}")
            errors += 1
            line["eval"] = {"pass": False, "reason": str(e)}
            processed_lines.append(line)
            continue

        status = "PASS" if is_valid else "FAIL"
        if is_valid:
            passed += 1
        else:
            failed += 1

        print(f"  [{query_name}] {status} - {reason}")
        line["eval"] = {"pass": is_valid, "reason": reason}
        processed_lines.append(line)

    # Write processed output
    processed_path = repo_root / "mxdatasets" / f"{args.benchmark}_output_processed.jsonl"
    with open(processed_path, "w") as f:
        for pl in processed_lines:
            f.write(json.dumps(pl) + "\n")
    print(f"\nWrote {len(processed_lines)} lines to {processed_path}")

    total = passed + failed + errors
    print(f"\n{'='*40}")
    print(f"Results: {passed}/{total} passed ({passed/total*100:.1f}%)" if total else "No queries evaluated")
    if failed:
        print(f"  Failed: {failed}")
    if errors:
        print(f"  Errors: {errors}")


if __name__ == "__main__":
    main()
