# mxscripts

Scripts to convert DataAgentBench datasets for our eval agent and evaluate results.

## 1. Generate input + connections for a benchmark

```bash
uv run mxscripts/convert_to_jsonl.py <benchmark> [<benchmark> ...]
uv run mxscripts/convert_to_jsonl.py --all
```

Example:
```bash
uv run mxscripts/convert_to_jsonl.py stockindex stockmarket
```

Outputs in `mxdatasets/`:
- `<benchmark>_input.jsonl` — one query per line with `allowed_connections`
- `<benchmark>_connections.json` — database connection configs

For postgres / mongo benchmarks the connection configs point at `localhost`
ports served by the compose stack below. Bring those servers up and load
their dumps before running the eval:

```bash
docker compose -f mxscripts/docker-compose.yml up -d
uv run mxscripts/setup_dbs.py --all          # or specific benchmarks
```

The compose file caps each service at 512 MB RAM / 1 CPU. Tear down with
`docker compose -f mxscripts/docker-compose.yml down -v` (the `-v` also
drops the data volumes so reloads start clean).

## 2. Evaluate agent output

After running the eval agent (which produces `mxdatasets/<benchmark>_output.jsonl`):

```bash
uv run mxscripts/eval_output.py <benchmark> [<benchmark> ...]
uv run mxscripts/eval_output.py --all
```

`--all` evaluates every benchmark that has an `_output.jsonl` in `mxdatasets/`
and prints a per-benchmark summary plus a grand total.

For each benchmark it reads `mxdatasets/<benchmark>_output.jsonl`, runs each
query's `query_<benchmark>/<query_id>/validate.py`, and writes
`mxdatasets/<benchmark>_output_processed.jsonl` (original output + `eval` key
with pass/fail and reason).
