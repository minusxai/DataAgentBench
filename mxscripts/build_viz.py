"""Build an HTML visualization from a finfin.jsonl eval file.

Usage:
    uv run python mxscripts/build_viz.py mxdatasets/finfinsub/fin.jsonl mxscripts/viz.html
"""

import json
import sys
from collections import defaultdict
from pathlib import Path


def parse(input_path: str) -> list[dict]:
    """Parse the JSONL and aggregate stats per dataset."""
    datasets: dict[str, dict] = defaultdict(lambda: {
        "queries": defaultdict(list),
        "total_tool_calls": 0,
        "total_duration_ms": 0,
        "tool_breakdown": defaultdict(int),
        "total_log_turns": 0,
    })

    with open(input_path) as f:
        for line in f:
            entry = json.loads(line)
            ds = entry["benchmark"]
            d = datasets[ds]
            qid = entry["input"]["query_id"]
            ev = entry["eval"]
            d["queries"][qid].append(ev["pass"])
            d["total_duration_ms"] += entry.get("duration_ms", 0)

            log = entry.get("log", [])
            d["total_log_turns"] += len(log)
            for e in log:
                if e.get("role") == "toolResult":
                    d["total_tool_calls"] += 1
                    d["tool_breakdown"][e.get("toolName", "unknown")] += 1

    rows = []
    for ds_name in sorted(datasets.keys(), key=str.lower):
        d = datasets[ds_name]
        queries = d["queries"]
        n_queries = len(queries)
        n_runs = sum(len(runs) for runs in queries.values())
        n_pass = sum(sum(runs) for runs in queries.values())
        per_q_acc = {qid: sum(runs) / len(runs) for qid, runs in queries.items()}
        macro_acc = sum(per_q_acc.values()) / len(per_q_acc) if per_q_acc else 0

        avg_duration_s = (d["total_duration_ms"] / n_runs / 1000) if n_runs else 0
        avg_turns = d["total_log_turns"] / n_runs if n_runs else 0
        avg_tool_calls = d["total_tool_calls"] / n_runs if n_runs else 0

        query_details = []
        for qid in sorted(queries.keys(), key=lambda x: int(x.replace("query", ""))):
            runs = queries[qid]
            query_details.append({
                "query_id": qid,
                "pass_rate": sum(runs) / len(runs),
                "n_runs": len(runs),
                "n_pass": sum(runs),
            })

        rows.append({
            "dataset": ds_name,
            "n_queries": n_queries,
            "n_runs": n_runs,
            "n_pass": n_pass,
            "pass_rate": n_pass / n_runs if n_runs else 0,
            "macro_acc": macro_acc,
            "avg_duration_s": round(avg_duration_s, 1),
            "avg_turns": round(avg_turns, 1),
            "avg_tool_calls": round(avg_tool_calls, 1),
            "total_tool_calls": d["total_tool_calls"],
            "tool_breakdown": dict(d["tool_breakdown"]),
            "query_details": query_details,
        })

    return rows


def build_html(rows: list[dict], input_path: str) -> str:
    total_runs = sum(r["n_runs"] for r in rows)
    total_pass = sum(r["n_pass"] for r in rows)
    total_queries = sum(r["n_queries"] for r in rows)
    overall_pass_rate = total_pass / total_runs if total_runs else 0
    overall_macro = sum(r["macro_acc"] for r in rows) / len(rows) if rows else 0

    rows_json = json.dumps(rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DAB Results — {Path(input_path).stem}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f1117; color: #e1e4e8; padding: 24px; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; color: #f0f0f0; }}
  .subtitle {{ color: #8b949e; font-size: 0.85rem; margin-bottom: 20px; }}
  .cards {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px 20px; min-width: 150px; }}
  .card .label {{ font-size: 0.75rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.05em; }}
  .card .value {{ font-size: 1.6rem; font-weight: 700; margin-top: 4px; }}
  .card .value.green {{ color: #3fb950; }}
  .card .value.blue {{ color: #58a6ff; }}
  .card .value.orange {{ color: #d29922; }}
  table {{ width: 100%; border-collapse: collapse; background: #161b22; border-radius: 8px; overflow: hidden; border: 1px solid #30363d; }}
  th {{ background: #1c2128; padding: 10px 14px; text-align: left; font-size: 0.8rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.04em; cursor: pointer; user-select: none; white-space: nowrap; }}
  th:hover {{ color: #e1e4e8; }}
  th .arrow {{ font-size: 0.65rem; margin-left: 4px; }}
  td {{ padding: 10px 14px; border-top: 1px solid #21262d; font-size: 0.9rem; }}
  tr:hover td {{ background: #1c2128; }}
  tr.expanded td {{ background: #1c2128; }}
  .bar-cell {{ min-width: 180px; }}
  .bar-bg {{ background: #21262d; border-radius: 4px; height: 20px; position: relative; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
  .bar-fill.high {{ background: #3fb950; }}
  .bar-fill.mid {{ background: #d29922; }}
  .bar-fill.low {{ background: #f85149; }}
  .bar-label {{ position: absolute; right: 6px; top: 50%; transform: translateY(-50%); font-size: 0.75rem; font-weight: 600; }}
  .detail-row td {{ padding: 0; }}
  .detail-content {{ padding: 12px 20px; background: #0d1117; }}
  .detail-content h3 {{ font-size: 0.85rem; color: #8b949e; margin-bottom: 8px; }}
  .detail-grid {{ display: flex; gap: 24px; flex-wrap: wrap; }}
  .detail-grid .section {{ flex: 1; min-width: 250px; }}
  .mini-table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
  .mini-table th {{ background: transparent; padding: 4px 8px; border-bottom: 1px solid #30363d; font-size: 0.72rem; }}
  .mini-table td {{ padding: 4px 8px; border: none; }}
  .pill {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.75rem; font-weight: 600; }}
  .pill.pass {{ background: #1a3a2a; color: #3fb950; }}
  .pill.fail {{ background: #3d1a1a; color: #f85149; }}
  .clickable {{ cursor: pointer; }}
</style>
</head>
<body>
<h1>DAB Benchmark Results</h1>
<p class="subtitle">{Path(input_path).name} &mdash; {total_queries} queries across {len(rows)} datasets, {total_runs} total runs</p>

<div class="cards">
  <div class="card"><div class="label">Macro Accuracy (Leaderboard)</div><div class="value green">{overall_macro:.1%}</div></div>
  <div class="card"><div class="label">Raw Pass Rate</div><div class="value blue">{overall_pass_rate:.1%}</div></div>
  <div class="card"><div class="label">Datasets</div><div class="value">{len(rows)}</div></div>
  <div class="card"><div class="label">Total Queries</div><div class="value">{total_queries}</div></div>
  <div class="card"><div class="label">Total Runs</div><div class="value">{total_runs}</div></div>
</div>

<table id="main-table">
<thead>
<tr>
  <th data-key="dataset">Dataset <span class="arrow"></span></th>
  <th data-key="macro_acc">Accuracy <span class="arrow"></span></th>
  <th data-key="pass_rate">Pass Rate <span class="arrow"></span></th>
  <th data-key="n_queries">Queries <span class="arrow"></span></th>
  <th data-key="n_pass">Passed <span class="arrow"></span></th>
  <th data-key="n_runs">Runs <span class="arrow"></span></th>
  <th data-key="avg_tool_calls">Avg Tool Calls <span class="arrow"></span></th>
  <th data-key="avg_turns">Avg Turns <span class="arrow"></span></th>
  <th data-key="avg_duration_s">Avg Duration (s) <span class="arrow"></span></th>
</tr>
</thead>
<tbody id="table-body"></tbody>
</table>

<script>
const DATA = {rows_json};
let sortKey = "dataset";
let sortAsc = true;
let expandedIdx = -1;

function barClass(v) {{ return v >= 0.6 ? "high" : v >= 0.3 ? "mid" : "low"; }}

function render() {{
  const sorted = [...DATA].sort((a, b) => {{
    let va = a[sortKey], vb = b[sortKey];
    if (typeof va === "string") va = va.toLowerCase();
    if (typeof vb === "string") vb = vb.toLowerCase();
    return (va < vb ? -1 : va > vb ? 1 : 0) * (sortAsc ? 1 : -1);
  }});

  document.querySelectorAll("th .arrow").forEach(el => el.textContent = "");
  const activeHeader = document.querySelector(`th[data-key="${{sortKey}}"] .arrow`);
  if (activeHeader) activeHeader.textContent = sortAsc ? " ▲" : " ▼";

  const tbody = document.getElementById("table-body");
  tbody.innerHTML = "";

  sorted.forEach((row, i) => {{
    const tr = document.createElement("tr");
    tr.className = "clickable" + (expandedIdx === i ? " expanded" : "");
    tr.onclick = () => {{ expandedIdx = expandedIdx === i ? -1 : i; render(); }};
    tr.innerHTML = `
      <td>${{row.dataset}}</td>
      <td class="bar-cell">
        <div class="bar-bg">
          <div class="bar-fill ${{barClass(row.macro_acc)}}" style="width:${{(row.macro_acc*100).toFixed(1)}}%"></div>
          <span class="bar-label">${{(row.macro_acc*100).toFixed(1)}}%</span>
        </div>
      </td>
      <td>${{(row.pass_rate*100).toFixed(1)}}%</td>
      <td>${{row.n_queries}}</td>
      <td>${{row.n_pass}} / ${{row.n_runs}}</td>
      <td>${{row.n_runs}}</td>
      <td>${{row.avg_tool_calls}}</td>
      <td>${{row.avg_turns}}</td>
      <td>${{row.avg_duration_s}}</td>
    `;
    tbody.appendChild(tr);

    if (expandedIdx === i) {{
      const detailTr = document.createElement("tr");
      detailTr.className = "detail-row";
      const td = document.createElement("td");
      td.colSpan = 9;
      let qrows = row.query_details.map(q => `
        <tr>
          <td>${{q.query_id}}</td>
          <td><span class="pill ${{q.pass_rate >= 0.5 ? 'pass' : 'fail'}}">${{(q.pass_rate*100).toFixed(0)}}%</span></td>
          <td>${{q.n_pass}} / ${{q.n_runs}}</td>
        </tr>`).join("");
      let trows = Object.entries(row.tool_breakdown)
        .sort((a,b) => b[1]-a[1])
        .map(([name, count]) => `<tr><td>${{name}}</td><td>${{count}}</td></tr>`).join("");
      td.innerHTML = `<div class="detail-content"><div class="detail-grid">
        <div class="section"><h3>Per-Query Breakdown</h3>
          <table class="mini-table"><thead><tr><th>Query</th><th>Pass Rate</th><th>Passed</th></tr></thead><tbody>${{qrows}}</tbody></table>
        </div>
        <div class="section"><h3>Tool Usage (total across all runs)</h3>
          <table class="mini-table"><thead><tr><th>Tool</th><th>Calls</th></tr></thead><tbody>${{trows}}</tbody></table>
        </div>
      </div></div>`;
      detailTr.appendChild(td);
      tbody.appendChild(detailTr);
    }}
  }});
}}

document.querySelectorAll("th[data-key]").forEach(th => {{
  th.addEventListener("click", () => {{
    const key = th.dataset.key;
    if (sortKey === key) sortAsc = !sortAsc;
    else {{ sortKey = key; sortAsc = key === "dataset"; }}
    render();
  }});
}});

render();
</script>
</body>
</html>"""


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <input_jsonl> [output_html]")
        sys.exit(1)
    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "mxscripts/viz.html"
    rows = parse(input_path)
    html = build_html(rows, input_path)
    Path(output_path).write_text(html)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
