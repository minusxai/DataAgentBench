# mxscripts

Scripts to convert DataAgentBench datasets for our eval agent and evaluate results.

## 1. Generate input + connections for a benchmark

```bash
uv run mxscripts/convert_to_jsonl.py <benchmark>
```

Example:
```bash
uv run mxscripts/convert_to_jsonl.py stockindex
```

Outputs in `mxdatasets/`:
- `<benchmark>_input.jsonl` — one query per line with `allowed_connections`
- `<benchmark>_connections.json` — database connection configs

## 2. Evaluate agent output

After running the eval agent (which produces `mxdatasets/<benchmark>_output.jsonl`):

```bash
uv run mxscripts/eval_output.py <benchmark>
```

Example:
```bash
uv run mxscripts/eval_output.py stockindex
```

Reads `mxdatasets/<benchmark>_output.jsonl`, runs each query's validator, and writes `mxdatasets/<benchmark>_output_processed.jsonl` (original output + `eval` key with pass/fail and reason).
