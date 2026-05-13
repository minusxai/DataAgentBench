# mxscripts

Scripts to convert DataAgentBench datasets for our eval agent and evaluate results.

## 1. Generate input + connections for a benchmark

```bash
uv run mxscripts/convert_to_jsonl.py <benchmark> [<benchmark> ...]
uv run mxscripts/convert_to_jsonl.py --all
```

Install pyaml
```bash
uv pip install pyyaml
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

## 2. Run the eval agent

The eval agent lives in the **minusx-bi-new** repo. It auto-discovers every
`<benchmark>_input.jsonl` here and writes `<benchmark>_output.jsonl` next to
each input.

```bash
cd /path/to/minusx-bi-new/frontend
DAB_BENCH_BASE_DIR=/path/to/DataAgentBench/mxdatasets npm run benchmark:dab
```

The agent supports resume (skips rows already persisted), per-row /
per-dataset timeouts, dataset filtering, and a row-limit cap. See
[`frontend/benchmarks/README.md`](https://github.com/minusxai/minusx-bi-new/blob/main/frontend/benchmarks/README.md)
for the full env-var reference (`DAB_BENCH_DATASETS`, `DAB_BENCH_RERUN`,
`DAB_QUESTION_TIMEOUT`, `DAB_DATASET_TIMEOUT`, etc.).

## 3. Evaluate agent output

After the eval agent has produced `mxdatasets/<benchmark>_output.jsonl`:

```bash
PYTHONPATH=. uv run mxscripts/eval_output.py <benchmark> [<benchmark> ...]
PYTHONPATH=. uv run mxscripts/eval_output.py --all
```

> **Important**: set `PYTHONPATH=.` (i.e. the repo root). Some validators
> import `common_scaffold` for shared helpers; without it on the path
> they raise `No module named 'common_scaffold'` and rows count as
> infrastructure errors rather than honest pass/fail.

`--all` evaluates every benchmark that has an `_output.jsonl` in `mxdatasets/`
and prints a per-benchmark summary plus a grand total.

For each benchmark it reads `mxdatasets/<benchmark>_output.jsonl`, runs each
query's `query_<benchmark>/<query_id>/validate.py`, and writes a combined
results JSONL (original output + `eval` key with pass/fail and reason).

By default the combined results are written to
`mxdatasets/evalrun_<timestamp>.jsonl`. Pass `--file` to choose a custom path:

```bash
PYTHONPATH=. uv run mxscripts/eval_output.py --all --file ~/Downloads/output.jsonl
```
