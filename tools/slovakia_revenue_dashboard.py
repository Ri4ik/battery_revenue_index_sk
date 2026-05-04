"""
Web dashboard for Slovakia single-market JSON results (daily revenues).

Run from repository root:
    python tools/slovakia_revenue_dashboard.py

Then open http://127.0.0.1:5050/ in a browser.
"""

import json
import os
import re
import sys
import webbrowser
from datetime import date, datetime as dt_datetime
from pathlib import Path
from threading import Timer
from typing import Dict, List, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
from flask import Flask, redirect, render_template_string, request, url_for

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

RESULT_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_results_([A-Za-z0-9+]+)\.json$")


def discover_result_roots(results_dir: Path) -> List[Path]:
    if not results_dir.is_dir():
        return []
    roots: List[Path] = []
    for p in sorted(results_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not p.is_dir():
            continue
        if any(RESULT_NAME_RE.match(f.name) for f in p.iterdir() if f.suffix == ".json"):
            roots.append(p)
    return roots


def parse_result_files(folder: Path) -> pd.DataFrame:
    rows: List[dict] = []
    for f in sorted(folder.glob("*_results_*.json")):
        m = RESULT_NAME_RE.match(f.name)
        if not m:
            continue
        day_s, market = m.group(1), m.group(2)
        with open(f, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        dr = (payload.get("results") or {}).get("daily_revenue") or {}
        val = dr.get("Total")
        if val is None:
            val = dr.get(market)
        if val is None:
            for k, v in dr.items():
                if k != "Total" and isinstance(v, (int, float)):
                    val = v
                    break
        if val is None:
            continue
        rows.append({"day": day_s, "market": market, "daily_revenue_eur": float(val)})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["day"] = pd.to_datetime(df["day"])
    return df


def folder_date_bounds(folder: Path) -> Optional[Tuple[pd.Timestamp, pd.Timestamp]]:
    days: List[pd.Timestamp] = []
    for f in folder.glob("*_results_*.json"):
        m = RESULT_NAME_RE.match(f.name)
        if not m:
            continue
        try:
            days.append(pd.to_datetime(m.group(1)))
        except Exception:
            continue
    if not days:
        return None
    return min(days), max(days)


def pick_default_folder(roots: List[Path]) -> Path:
    """Pick folder with the longest date span; fallback to the first one."""
    if not roots:
        raise ValueError("No result roots provided.")
    best = roots[0]
    best_span = -1
    for p in roots:
        bounds = folder_date_bounds(p)
        if not bounds:
            continue
        span = int((bounds[1] - bounds[0]).days)
        if span > best_span:
            best_span = span
            best = p
    return best


def build_figure(
    df: pd.DataFrame,
    markets: List[str],
    mode: str,
    ma_window: Optional[int],
    show_cumulative: bool,
    aggregation: str,
) -> go.Figure:
    sub = df[df["market"].isin(markets)].copy()
    if sub.empty:
        fig = go.Figure()
        fig.update_layout(title="Pre vybrané trhy alebo obdobie nie sú dostupné údaje")
        return fig

    pivot = sub.pivot_table(
        index="day", columns="market", values="daily_revenue_eur", aggfunc="sum"
    ).fillna(0)
    pivot = pivot.sort_index()
    for m in markets:
        if m not in pivot.columns:
            pivot[m] = 0.0
    pivot = pivot[[c for c in markets if c in pivot.columns]]
    # Base unit in source JSON: EUR/day (daily revenue).
    if aggregation == "annualized":
        # Convert daily EUR to annualized kEUR/MW/year.
        # Current benchmark uses 1 MW systems, so normalization by MW is 1.
        pivot = pivot * 365.0 / 1000.0
        y_title = "Anualizovaný výnos batérie (kEUR/MW/rok)"
    elif aggregation == "avg30":
        pivot = pivot.rolling(window=30, min_periods=1).mean()
        y_title = "30-dňový priemerný výnos batérie (EUR/deň)"
    elif aggregation == "avg365":
        pivot = pivot.rolling(window=365, min_periods=1).mean()
        y_title = "365-dňový priemerný výnos batérie (EUR/deň)"
    else:
        y_title = "Denný výnos (EUR/deň)"

    total = pivot.sum(axis=1)

    fig = go.Figure()
    if mode == "stack":
        for col in pivot.columns:
            fig.add_trace(
                go.Bar(x=pivot.index, y=pivot[col], name=col, hovertemplate="%{y:.2f} €<extra></extra>")
            )
        fig.update_layout(barmode="stack")
    else:
        for col in pivot.columns:
            fig.add_trace(
                go.Scatter(
                    x=pivot.index,
                    y=pivot[col],
                    mode="lines+markers",
                    name=col,
                    hovertemplate="%{y:.2f} €<extra></extra>",
                )
            )

    if ma_window and ma_window > 1:
        ma = total.rolling(window=ma_window, min_periods=1).mean()
        fig.add_trace(
            go.Scatter(
                x=pivot.index,
                y=ma,
                name="Súčet trhov (%d-dňový priemer)" % ma_window,
                line=dict(width=3, dash="dash"),
                hovertemplate="%{y:.2f} €<extra></extra>",
            )
        )

    if show_cumulative:
        cum = total.cumsum()
        fig.add_trace(
            go.Scatter(
                x=pivot.index,
                y=cum,
                name="Kumulatívny súčet",
                yaxis="y2",
                line=dict(width=2),
                hovertemplate="%{y:.2f} €<extra></extra>",
            )
        )
        fig.update_layout(
            yaxis=dict(title=y_title),
            yaxis2=dict(title="Kumulatívny súčet", overlaying="y", side="right", showgrid=False),
        )
    else:
        fig.update_layout(yaxis=dict(title=y_title))

    fig.update_layout(
        title="Slovenský index výnosov batérií - denné výnosy podľa trhov",
        xaxis_title="Dátum",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        template="plotly_white",
        height=560,
        hovermode="x unified",
    )
    return fig


PAGE = """
<!doctype html>
<html lang="sk">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Slovenský index výnosov batérií</title>
  <style>
    :root {
      --bg: #f4f8fc;
      --card: #ffffff;
      --line: #dde5ef;
      --text: #11243c;
      --muted: #4e5d73;
      --brand: #0f4f8a;
      --brand-dark: #083964;
    }
    * { box-sizing: border-box; }
    body { font-family: "Segoe UI", system-ui, sans-serif; margin: 0; background: var(--bg); color: var(--text); }
    .container { max-width: 1260px; margin: 24px auto; padding: 0 16px; }
    .title { font-size: 48px; font-weight: 800; text-align: center; margin: 0 0 18px; letter-spacing: 0.2px; }
    .card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; box-shadow: 0 8px 20px rgba(8, 38, 66, 0.08); }
    .controls { padding: 18px 20px 14px; margin-bottom: 18px; }
    .controls form { display: grid; grid-template-columns: repeat(4, minmax(170px, 1fr)); gap: 14px; align-items: end; }
    .control-group { display: flex; flex-direction: column; gap: 6px; }
    .control-group label { font-size: 12px; text-transform: uppercase; font-weight: 700; color: var(--muted); letter-spacing: 0.3px; }
    .control-group select, .control-group input[type="date"] {
      width: 100%;
      border-radius: 999px;
      border: 1px solid #0d3f6f;
      background: linear-gradient(180deg, #145d9d, #0d4b80);
      color: #fff;
      min-height: 38px;
      padding: 8px 14px;
      font-weight: 600;
    }
    .control-group select option {
      background: #ffffff;
      color: #153c61;
    }
    .control-group select[multiple] {
      border-radius: 14px;
      min-height: 156px;
      background: #f8fbff;
      color: #13395e;
      border: 1px solid #bcd0e4;
      padding: 8px;
      font-weight: 500;
    }
    .markets-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
      gap: 8px;
      margin-top: 2px;
    }
    .market-chip {
      display: flex;
      align-items: center;
      gap: 8px;
      border: 1px solid #c8d8ea;
      background: #f8fbff;
      color: #123a5f;
      border-radius: 999px;
      padding: 7px 10px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      user-select: none;
    }
    .market-chip input[type="checkbox"] {
      width: 15px;
      height: 15px;
      accent-color: #0d4b80;
      cursor: pointer;
      margin: 0;
    }
    .small-note { margin: 2px 0 0; color: var(--muted); font-size: 12px; }
    .inline-options { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .inline-options label { font-size: 14px; text-transform: none; color: var(--text); }
    .actions { grid-column: 1 / -1; display: flex; gap: 10px; align-items: center; }
    button {
      background: linear-gradient(180deg, var(--brand), var(--brand-dark));
      color: #fff; border: 0; border-radius: 999px; padding: 10px 20px;
      font-size: 14px; font-weight: 700; cursor: pointer;
    }
    button:hover { filter: brightness(1.06); }
    .link { font-size: 13px; color: var(--muted); }
    .range-info {
      grid-column: 1 / -1;
      margin-top: -4px;
      padding: 8px 12px;
      border-radius: 10px;
      background: #eef5fd;
      color: #1d4467;
      font-size: 13px;
      border: 1px solid #d7e5f4;
    }
    .chart-wrap { padding: 12px 14px; margin-bottom: 18px; }
    table { border-collapse: collapse; margin-top: 1rem; width: 100%; background: #fff; border-radius: 12px; overflow: hidden; }
    th, td { border: 1px solid #e7edf5; padding: 0.45rem 0.7rem; text-align: right; }
    th:first-child, td:first-child { text-align: left; }
    caption { font-weight: 700; text-align: left; margin-bottom: 0.35rem; color: var(--text); }
    .muted { color: var(--muted); font-size: 0.92rem; margin: 0 0 14px; text-align: center; }
    @media (max-width: 1020px) {
      .controls form { grid-template-columns: repeat(2, minmax(170px, 1fr)); }
      .title { font-size: 36px; }
    }
    @media (max-width: 640px) {
      .controls form { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="container">
    <h1 class="title">ISEA Index výnosov batérií</h1>
    <p class="muted">
      Slovenská verzia dashboardu s dátami z <code>results/</code>.
      Inšpirácia dizajnom:
      <a href="https://battery-charts.de/revenue-index/#daily-revenues">battery-charts.de</a>.
    </p>

    <section class="card controls">
      <form method="get" action="{{ url }}">
        <div class="control-group">
          <label>Zdroj výsledkov</label>
          <select name="folder">
          {% for name in folder_names %}
          <option value="{{ name }}" {% if name == folder %}selected{% endif %}>{{ name }}</option>
          {% endfor %}
          </select>
        </div>

        <div class="control-group">
          <label>Obdobie od</label>
          <input type="date" name="start" value="{{ start }}"/>
        </div>

        <div class="control-group">
          <label>Obdobie do</label>
          <input type="date" name="end" value="{{ end }}"/>
        </div>

        <div class="control-group">
          <label>Typ grafu</label>
          <div class="inline-options">
            <label><input type="radio" name="mode" value="stack" {% if mode == 'stack' %}checked{% endif %}/> Skladané stĺpce</label>
            <label><input type="radio" name="mode" value="lines" {% if mode == 'lines' %}checked{% endif %}/> Čiary</label>
          </div>
        </div>

        <div class="control-group">
          <label>Agregácia</label>
          <select name="agg">
            <option value="absolute" {% if agg == 'absolute' %}selected{% endif %}>Absolútne hodnoty</option>
            <option value="avg30" {% if agg == 'avg30' %}selected{% endif %}>30-dňový priemer</option>
            <option value="avg365" {% if agg == 'avg365' %}selected{% endif %}>365-dňový priemer</option>
            <option value="annualized" {% if agg == 'annualized' %}selected{% endif %}>Anualizované (kEUR/MW/rok)</option>
          </select>
        </div>

        <div class="control-group">
          <label>Trhy na grafe</label>
          <div class="markets-grid">
            {% for m in all_markets %}
            <label class="market-chip">
              <input type="checkbox" name="markets" value="{{ m }}" {% if m in selected %}checked{% endif %}/>
              <span>{{ m }}</span>
            </label>
            {% endfor %}
          </div>
          <p class="small-note">Výber trhov: jednoducho kliknutím na zaškrtávacie políčko.</p>
        </div>

        <div class="control-group">
          <label>Kĺzavý priemer súčtu (dni)</label>
          <select name="ma">
          {% for w in ma_options %}
          <option value="{{ w }}" {% if w == ma %}selected{% endif %}>{{ w }}</option>
          {% endfor %}
          </select>
        </div>

        <div class="control-group">
          <label>Doplnky</label>
          <div class="inline-options">
            <label><input type="checkbox" name="cum" value="1" {% if cum %}checked{% endif %}/> Zobraziť kumulatívny súčet na 2. osi</label>
          </div>
        </div>

        <div class="actions">
          <button type="submit">Použiť filtre</button>
          <span class="link">Dashboard vychádza z metodiky
            <a href="https://battery-charts.de/revenue-index/#daily-revenues">ISEA Revenue Index</a>.
          </span>
        </div>
        <div class="range-info">
          Dostupný rozsah dát vo zvolenom priečinku: <strong>{{ data_min }}</strong> až <strong>{{ data_max }}</strong>.
          Ak potrebuješ celý rok, vyber priečinok s dlhším rozsahom.
        </div>
      </form>
    </section>

    <section class="card chart-wrap">
      {{ chart_html|safe }}
    </section>

    {% if summary_rows %}
    <table>
      <caption>Súčet za vybrané obdobie (€)</caption>
      <thead><tr><th>Trh</th><th>Súčet EUR</th></tr></thead>
      <tbody>
        {% for m, v in summary_rows %}
        <tr><td>{{ m }}</td><td>{{ '%.2f' % v }}</td></tr>
        {% endfor %}
      </tbody>
    </table>
    {% endif %}
  </div>
</body>
</html>
"""


def create_app(repo: Path) -> Flask:
    app = Flask(__name__)
    results_dir = repo / "results"

    @app.route("/")
    def index():
        roots = discover_result_roots(results_dir)
        if not roots:
            return (
                "<h1>Chýbajú výsledky</h1><p>V priečinku results/ nie sú JSON "
                "<code>YYYY-MM-DD_results_*.json</code>.</p>",
                404,
            )

        folder_names = [p.name for p in roots]
        default_folder = pick_default_folder(roots).name
        folder = request.args.get("folder") or default_folder
        folder_path = results_dir / folder
        if not folder_path.is_dir():
            return redirect(url_for("index"))

        df = parse_result_files(folder_path)
        if df.empty:
            return "<h1>Prázdne dáta</h1><p>Vo vybranom priečinku nie sú vhodné JSON súbory.</p>", 404

        all_markets = sorted(df["market"].unique())
        dmin = df["day"].min().date()
        dmax = df["day"].max().date()

        selected = request.args.getlist("markets")
        if not selected:
            selected = list(all_markets)

        start_s = request.args.get("start") or dmin.isoformat()
        end_s = request.args.get("end") or dmax.isoformat()
        try:
            start_d = dt_datetime.strptime(start_s, "%Y-%m-%d").date()
            end_d = dt_datetime.strptime(end_s, "%Y-%m-%d").date()
        except ValueError:
            start_d, end_d = dmin, dmax

        mode = request.args.get("mode") or "stack"
        if mode not in ("stack", "lines"):
            mode = "stack"
        agg = request.args.get("agg") or "absolute"
        if agg not in ("absolute", "avg30", "avg365", "annualized"):
            agg = "absolute"
        ma = int(request.args.get("ma") or "1")
        if ma not in (1, 3, 7, 14, 30):
            ma = 1
        cum = request.args.get("cum") == "1"

        mask = (df["day"] >= pd.Timestamp(start_d)) & (df["day"] <= pd.Timestamp(end_d))
        filt = df.loc[mask]

        fig = build_figure(
            filt,
            selected,
            mode,
            ma if ma > 1 else None,
            cum,
            agg,
        )
        chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn", config={"displayModeBar": True})

        sub = filt[filt["market"].isin(selected)]
        summary = sub.groupby("market")["daily_revenue_eur"].sum().sort_values(ascending=False)
        summary_rows = [(m, float(v)) for m, v in summary.items()]

        return render_template_string(
            PAGE,
            url=request.path,
            folder_names=folder_names,
            folder=folder,
            start=start_d.isoformat(),
            end=end_d.isoformat(),
            all_markets=all_markets,
            selected=selected,
            mode=mode,
            agg=agg,
            ma_options=[1, 3, 7, 14, 30],
            ma=ma,
            cum=cum,
            chart_html=chart_html,
            summary_rows=summary_rows,
            data_min=dmin.isoformat(),
            data_max=dmax.isoformat(),
        )

    return app


def main():
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5050)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()

    app = create_app(_REPO)
    if not args.no_browser:

        def _open():
            webbrowser.open("http://%s:%s/" % (args.host, args.port))

        Timer(1.0, _open).start()
    print("Otvorte http://%s:%s/ (Ctrl+C pre zastavenie)" % (args.host, args.port))
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
