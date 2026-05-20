"""Convert finfin.jsonl (or fin.jsonl) to the leaderboard submission JSON format.

Usage:
    uv run python mxscripts/convert_to_submission.py <input_jsonl> <output_json>

Example:
    uv run python mxscripts/convert_to_submission.py mxdatasets/finfinsub/fin.jsonl leaderboard_submissions/minusx_results.json
"""

import json
import sys


def extract_answer(log: list) -> str:
    """Extract the answer from the last log entry's content text."""
    if not log:
        return ""
    last = log[-1]
    content = last.get("content", "")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block["text"]
    if isinstance(content, str):
        return content
    return ""


def convert(input_path: str, output_path: str):
    results = []
    with open(input_path, "r") as f:
        for line in f:
            entry = json.loads(line)
            dataset = entry["benchmark"]
            query_id = entry["input"]["query_id"]  # e.g. "query2"
            query_num = query_id.replace("query", "")
            run_idx = entry["eval"]["run_idx"]
            answer = extract_answer(entry.get("log", []))

            results.append({
                "dataset": dataset,
                "query": query_num,
                "run": str(run_idx),
                "answer": answer,
            })

    # Sort by dataset, query (numeric), run (numeric)
    results.sort(key=lambda r: (r["dataset"].lower(), int(r["query"]), int(r["run"])))

    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"Wrote {len(results)} entries to {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input_jsonl> <output_json>")
        sys.exit(1)
    convert(sys.argv[1], sys.argv[2])
