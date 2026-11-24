"""Battery Calculator pages of the dashboard (/kalkulacka).

Form -> scenario stored in the database and computed in a separate process -> progress page
(refreshes itself) -> results: KPIs, cash flow, SoH, historical revenue, sensitivity, CSV export.
Scenarios are separate from the official index and never change its published results.
"""

import io

import pandas as pd
import plotly.graph_objects as go
from flask import Blueprint, Response, abort, redirect, render_template_string, request, url_for

from bri import db
from bri.calculator import params as P, presets as PR, scenario, simulation

bp = Blueprint("calculator", __name__)
_migrated = False

COLORS = {"DA": "#0f4f8a", "IDM15": "#2a9d8f", "IDM60": "#8ab17d", "IMB": "#e76f51", "AFRR": "#e9c46a"}
MARKET_NAMES = {"DA": "DA", "IDM15": "IDM15", "IDM60": "IDM60", "IMB": "IMB", "AFRR": "aFRR"}


def _conn():
    global _migrated
    conn = db.connect()
    if not _migrated:
        db.migrate(conn)
        _migrated = True
    return conn


def num(value, digits=0):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "–"
    text = "{:,.{}f}".format(float(value), digits)
    return text.replace(",", " ").replace(".", ",")


def pct(value, digits=1):
    return "–" if value is None else num(value * 100, digits) + " %"


def years_text(value):
    return "nevráti sa" if value is None else num(value, 1) + " r."


STYLE = """
  :root { --bg:#f4f8fc; --card:#fff; --line:#dde5ef; --text:#11243c; --muted:#4e5d73; --brand:#0f4f8a;
          --brand-dark:#083964; --good:#1d7a46; --bad:#b3261e; }
  * { box-sizing: border-box; }
  body { font-family: "Segoe UI", system-ui, sans-serif; margin:0; background:var(--bg); color:var(--text); }
  .container { max-width:1260px; margin:24px auto; padding:0 16px; }
  h1 { font-size:40px; font-weight:800; text-align:center; margin:0 0 6px; }
  h2 { font-size:20px; margin:0 0 12px; }
  .muted { color:var(--muted); font-size:14px; text-align:center; margin:0 0 18px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px;
          box-shadow:0 8px 20px rgba(8,38,66,.08); padding:18px 20px; margin-bottom:18px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:12px 18px; }
  fieldset { border:1px solid var(--line); border-radius:12px; padding:12px 14px 14px; margin:0 0 14px; }
  legend { font-weight:700; padding:0 6px; }
  label { display:flex; flex-direction:column; gap:4px; font-size:13px; color:var(--muted); }
  label span.unit { font-size:11px; }
  input, select { border:1px solid #bcd0e4; border-radius:10px; padding:8px 10px; font-size:14px;
                  color:var(--text); background:#f8fbff; width:100%; }
  button, .button { background:linear-gradient(180deg,var(--brand),var(--brand-dark)); color:#fff; border:0;
           border-radius:999px; padding:10px 22px; font-size:14px; font-weight:700; cursor:pointer;
           text-decoration:none; display:inline-block; }
  .error { background:#fdecea; border:1px solid #f5c2bd; color:var(--bad); border-radius:10px; padding:10px 14px;
           margin-bottom:14px; }
  .note { background:#eef5fd; border:1px solid #d7e5f4; border-radius:10px; padding:10px 14px; font-size:13px;
          color:#1d4467; line-height:1.5; }
  .warn { background:#fff8e8; border:1px solid #f0d59a; color:#6a4d00; border-radius:10px; padding:10px 14px;
          font-size:13px; line-height:1.5; margin-bottom:14px; }
  .kpis { display:grid; grid-template-columns:repeat(auto-fill, minmax(190px, 1fr)); gap:12px; }
  .kpi { border:1px solid var(--line); border-radius:12px; padding:12px 14px; background:#f8fbff; }
  .kpi .v { font-size:22px; font-weight:800; margin-top:4px; }
  .kpi .l { font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.3px; }
  .pos { color:var(--good); } .neg { color:var(--bad); }
  table { border-collapse:collapse; width:100%; font-size:13px; }
  th, td { border:1px solid #e7edf5; padding:6px 8px; text-align:right; white-space:nowrap; }
  th:first-child, td:first-child { text-align:left; }
  th { background:#f3f7fc; }
  .scroll { overflow-x:auto; }
  .bar { height:14px; background:#e3ecf6; border-radius:999px; overflow:hidden; margin:12px 0; }
  .bar div { height:100%; background:linear-gradient(90deg,var(--brand),#2a9d8f); }
  .links { display:flex; gap:12px; flex-wrap:wrap; align-items:center; justify-content:center; margin-bottom:18px; }
  .links a { color:var(--brand); font-weight:600; }
  @media (max-width:640px) { h1 { font-size:28px; } .grid { grid-template-columns:1fr; } }
"""

FORM = """<!doctype html>
<html lang="sk"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Kalkulačka batérie</title><style>{{ style|safe }}</style></head><body><div class="container">
<h1>Kalkulačka batérie</h1>
<p class="muted">Nastavte batériu, trh a financovanie. Výnosy sa simulujú na historických cenách slovenských trhov
z databázy indexu, potom sa premietnu na celú dobu projektu s degradáciou batérie.</p>
<div class="links"><a href="/">← Index výnosov</a><a href="{{ url_for('calculator.method') }}">Metodika a predpoklady</a></div>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="post" action="{{ url_for('calculator.form') }}" class="card">
  <fieldset><legend>Typ batérie</legend><div class="grid">
    <label>Komerčný systém
      <select name="preset" id="preset">
        <option value="">Vlastná konfigurácia</option>{% for b in presets %}
        <option value="{{ b.id }}" {% if values.preset == b.id %}selected{% endif %}>{{ b.name }} – {{ b.unit }}</option>{% endfor %}
      </select></label>
    <label>Počet jednotiek (kontajnerov)
      <input type="number" name="preset_units" id="preset_units" min="1" max="1000" step="1"
             value="{{ values.preset_units or 1 }}"></label>
  </div>
  <div id="preset-info" class="note" style="margin:12px 0 0; display:none"></div>
  <p class="small" style="font-size:12px;color:var(--muted);margin:8px 0 0">Výber vyplní výkon, kapacitu, účinnosť
  a cenu podľa údajov výrobcu a cenového indexu ({{ price_source }}). Ostatné polia (trh, financovanie,
  poplatky, daň) treba nastaviť podľa projektu; vyplnené hodnoty sa dajú ďalej upraviť.</p></fieldset>
  <fieldset><legend>Trh a obdobie historických cien</legend><div class="grid">
    <label>Stratégia trhu
      <select name="strategy">{% for key, text in strategies %}
        <option value="{{ key }}" {% if values.strategy == key %}selected{% endif %}>{{ text }}</option>{% endfor %}
      </select></label>
    <label>Začiatok obdobia <input type="date" name="period_start" value="{{ values.period_start }}"
           min="{{ data_start }}" max="{{ data_end }}"></label>
    <label>Koniec obdobia <input type="date" name="period_end" value="{{ values.period_end }}"
           min="{{ data_start }}" max="{{ data_end }}"></label>
  </div>
  <p class="note" style="margin:12px 0 0">Dáta v databáze: {{ data_start or '–' }} – {{ data_end or '–' }}.
  Výnos obdobia sa prepočíta na jeden rok (priemer dňa × 365). Stratégia „najlepší trh v každom dni“ je horná
  hranica – trh sa vyberá až so znalosťou cien.</p></fieldset>
  {% for group, title in groups %}
  <fieldset><legend>{{ title }}</legend><div class="grid">
    {% for f in fields if f.group == group %}
    <label>{{ f.label }} <span class="unit">{{ f.unit }} (rozsah {{ f.lo }}–{{ f.hi }})</span>
      {% if f.name == 'replace_at_eol' %}
      <select name="{{ f.name }}"><option value="1" {% if values[f.name]|int == 1 %}selected{% endif %}>áno</option>
        <option value="0" {% if values[f.name]|int == 0 %}selected{% endif %}>nie</option></select>
      {% else %}
      <input type="number" step="any" name="{{ f.name }}" value="{{ values[f.name] }}" min="{{ f.lo }}" max="{{ f.hi }}">
      {% endif %}
    </label>
    {% endfor %}
  </div></fieldset>
  {% endfor %}
  <button type="submit">Vypočítať</button>
  <span class="muted" style="margin-left:10px">Prvý výpočet novej batérie trvá približne 1–3 minúty, opakovaný
  len niekoľko sekúnd.</span>
</form>
<script>
const PRESETS = {{ presets|tojson }};
const byId = Object.fromEntries(PRESETS.map(p => [p.id, p]));
const form = document.querySelector('form');
function showPreset(fill) {
  const p = byId[form.preset.value], box = document.getElementById('preset-info');
  if (!p) { box.style.display = 'none'; return; }
  const units = Math.max(1, parseInt(form.preset_units.value || '1', 10));
  if (fill) {
    for (const [k, v] of Object.entries(p.fill)) {
      if (form[k] && !['preset', 'preset_units'].includes(k)) form[k].value = v;
    }
    form.power_mw.value = +(units * p.unit_power_mw).toFixed(3);
  }
  const mw = units * p.unit_power_mw, mwh = mw * p.fill.duration_h;
  box.innerHTML = '<b>' + p.name + '</b> × ' + units + ' = ' + mw.toFixed(2) + ' MW / ' + mwh.toFixed(2) +
    ' MWh · blok ' + p.block + '<br>Účinnosť (round-trip): ' + p.rte + ' · záruka: ' + p.warranty +
    ' · životnosť v cykloch: ' + p.cycles + '<br>Cena zariadenia: ' + p.price + ' (' + p.price_note +
    '), prepočítané na ' + p.fill.capex_energy_eur_kwh + ' €/kWh' +
    (p.fill.capex_power_eur_kw ? ' + striedač ' + p.fill.capex_power_eur_kw + ' €/kW (odhad)' : '') +
    ' + inštalácia ' + p.fill.capex_installation_pct + ' % (odhad) · <a href="' + p.url + '" target="_blank" rel="noopener">zdroj</a>';
  box.style.display = 'block';
}
form.preset.addEventListener('change', () => showPreset(true));
form.preset_units.addEventListener('input', () => showPreset(true));
showPreset(false);
</script>
{% if recent %}<section class="card"><h2>Posledné výpočty</h2><div class="scroll"><table>
<tr><th>#</th><th>Vytvorený</th><th>Batéria</th><th>Trh</th><th>Stav</th><th>NPV projektu</th><th>IRR projektu</th></tr>
{% for r in recent %}<tr><td><a href="{{ url_for('calculator.result', scenario_id=r.id) }}">{{ r.id }}</a></td>
<td>{{ r.created }}</td><td>{{ r.battery }}</td><td>{{ r.strategy }}</td><td>{{ r.status }}</td>
<td>{{ r.npv }}</td><td>{{ r.irr }}</td></tr>{% endfor %}
</table></div></section>{% endif %}
</div></body></html>"""

PROGRESS = """<!doctype html>
<html lang="sk"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
{% if s.status in ('queued', 'running') %}<meta http-equiv="refresh" content="3">{% endif %}
<title>Kalkulačka batérie</title><style>{{ style|safe }}</style></head><body><div class="container">
<h1>Výpočet č. {{ s.scenario_id }}</h1>
<section class="card">
{% if s.status == 'failed' %}
  <div class="error">Výpočet zlyhal: {{ s.message }}</div>
  <a class="button" href="{{ edit_url }}">Upraviť parametre</a>
{% else %}
  <p>{{ 'Čaká na spustenie' if s.status == 'queued' else (s.message or 'Prebieha výpočet') }}…</p>
  <div class="bar"><div style="width: {{ (s.progress * 100)|round(0) }}%"></div></div>
  <p class="muted">Stránka sa obnovuje automaticky.</p>
{% endif %}
</section></div></body></html>"""

RESULT = """<!doctype html>
<html lang="sk"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Kalkulačka batérie – výsledok</title><style>{{ style|safe }}</style></head><body><div class="container">
<h1>Výsledok výpočtu č. {{ s.scenario_id }}</h1>
<p class="muted">{{ battery }} · {{ strategy }} · historické ceny {{ s.period_start }} – {{ s.period_end }}
({{ summary.days_used }} z {{ summary.days_in_period }} dní s dátami)</p>
<div class="links"><a href="{{ edit_url }}">Upraviť parametre</a>
<a href="{{ url_for('calculator.form') }}">Nový výpočet</a>
<a href="{{ url_for('calculator.csv', scenario_id=s.scenario_id) }}">Export CSV</a>
<a href="{{ url_for('calculator.method') }}">Metodika</a><a href="/">Index výnosov</a></div>
{% if summary.days_used < summary.days_in_period * 0.8 %}<div class="warn">Pre {{ summary.days_in_period -
summary.days_used }} dní obdobia chýbajú vstupné dáta zvoleného trhu; ročný výnos je priemer dostupných dní × 365.</div>{% endif %}
{% if params.strategy == 'BEST' %}<div class="warn">Stratégia „najlepší trh v každom dni“ predpokladá, že vopred
viete, ktorý trh bude v daný deň najvýnosnejší. Výsledok je horná hranica, nie realistický odhad.
V dňoch, keď niektorému trhu chýbajú dáta (napr. aFRR), sa vyberá z dostupných trhov.</div>{% endif %}
<section class="card"><h2>Ekonomická efektívnosť</h2><div class="kpis">
{% for label, value, cls in kpis %}<div class="kpi"><div class="l">{{ label }}</div>
<div class="v {{ cls }}">{{ value }}</div></div>{% endfor %}
</div></section>
<section class="card"><h2>Peňažné toky po rokoch</h2>{{ chart_cf|safe }}</section>
<section class="card"><h2>Výnosy a náklady po rokoch</h2>{{ chart_pl|safe }}</section>
<section class="card"><h2>Stav batérie (SoH)</h2>{{ chart_soh|safe }}</section>
<section class="card"><h2>Historické výnosy v zvolenom období</h2>{{ chart_hist|safe }}
<div class="scroll"><table><tr><th>Trh</th><th>Dni s dátami</th><th>Ročný výnos (€)</th><th>€/MW/rok</th>
<th>EFC/rok</th>{% if params.strategy == 'BEST' %}<th>Zvolený v dňoch</th>{% endif %}</tr>
{% for m in summary.by_market %}<tr><td>{{ names[m.market] }}</td><td>{{ m.days }}</td><td>{{ num(m.annual_revenue_eur) }}</td>
<td>{{ num(m.annual_revenue_eur / params.power_mw) }}</td><td>{{ num(m.annual_efc) }}</td>
{% if params.strategy == 'BEST' %}<td>{{ m.chosen_days }}</td>{% endif %}</tr>{% endfor %}
</table></div></section>
<section class="card"><h2>Citlivostná analýza (NPV projektu)</h2>{{ chart_tornado|safe }}</section>
<section class="card"><h2>Tabuľka po rokoch (€)</h2><div class="scroll"><table>
<tr>{% for c in year_headers %}<th>{{ c }}</th>{% endfor %}</tr>
{% for row in year_rows %}<tr>{% for v in row %}<td>{{ v }}</td>{% endfor %}</tr>{% endfor %}
</table></div></section>
<section class="card"><h2>Predpoklady výpočtu</h2><div class="scroll"><table>
{% for label, value in assumptions %}<tr><td>{{ label }}</td><td>{{ value }}</td></tr>{% endfor %}
</table></div>
<p class="note">Výsledok je modelový odhad na základe historických cien, nie investičné odporúčanie.
Predvolené hodnoty nákladov, degradácie, poplatkov a daní sú orientačné a treba ich nahradiť údajmi
z ponúk dodávateľov, od agregátora a podľa platných predpisov.</p></section>
</div></body></html>"""

METHOD = """<!doctype html>
<html lang="sk"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Kalkulačka batérie – metodika</title><style>{{ style|safe }}</style></head><body><div class="container">
<h1>Metodika kalkulačky</h1>
<div class="links"><a href="{{ url_for('calculator.form') }}">← Kalkulačka</a><a href="/">Index výnosov</a></div>
<section class="card" style="line-height:1.6">
<h2>1. Battery Modelling</h2>
<p>Batéria je daná výkonom (MW), dobou vybíjania (h, kapacita = výkon × doba), limitom cyklov za deň,
jednosmernou účinnosťou a rozsahom SoC. Stav batérie (SoH) klesá každý rok o kalendárnu zložku (% za rok)
a cyklickú zložku (% na 100 ekvivalentných plných cyklov, EFC). Počet EFC za rok sa určuje zo simulovaného
priebehu SoC (súčet |ΔSoC| / 2). Po poklese SoH na hranicu konca životnosti sa články voliteľne vymenia
(náklad = zvolené % z CAPEX energie) a SoH sa vráti na 100 %.</p>
<h2>2. Revenue Simulation</h2>
<p>Výnosy sa počítajú tými istými modelmi ako oficiálny index (DA, IDM15, IDM60, IMB – arbitráž na natívnych
intervaloch; aFRR – kapacita po 4-hodinových blokoch a energia z aktivácií SEPS) pre batériu 1 MW s danou dobou
vybíjania a výsledok sa vynásobí výkonom. Prah výhodnosti obchodu (opotrebenie) je rovnaký ako v indexe
(250 €/kWh, 10 rokov), aby sa pre konfigurácie indexu výsledky zhodovali. Trhy sa nesčítavajú – batéria
nemôže v tom istom čase predávať tú istú energiu na viacerých trhoch. Výnos zvoleného obdobia sa prepočíta na
rok, vynásobí dosiahnuteľnou časťou, ročnou zmenou výnosov a priemerným SoH roka.</p>
<h2>3. Investment Analysis</h2>
<p>CAPEX = (€/kWh × kapacita + €/kW × výkon) × (1 + doprava, inštalácia a pripojenie v %) + fixné náklady.
Pri výbere komerčného systému sa cena zariadenia preberá z cenového indexu Coloprice (stred zverejneného
rozpätia, USD prepočítané orientačným kurzom {{ eur_per_usd }} €/USD); pri blokoch DC sa pridáva odhad ceny
striedača {{ pcs }} €/kW a pre všetky systémy odhad inštalácie {{ inst }} %. Tieto odhady nie sú overené
údaje. OPEX = % z CAPEX ročne s infláciou.
Poplatok agregátorovi je % z výnosov, sieťové poplatky sa platia za nabitú energiu
(vybitá energia / účinnosť²). Daň: rovnomerné odpisy, strata sa prenáša do ďalších rokov, úroky sú daňovým
nákladom. Úver je anuitný. Výstupy: peňažné toky projektu (bez úveru) a vlastníka, NPV, IRR, jednoduchá
a diskontovaná doba návratnosti, LCOS (súčasná hodnota nákladov / súčasná hodnota vybitej energie, bez ceny
nabíjacej energie) a DSCR = (EBITDA − daň) / splátka úveru.</p>
<h2>4. Obmedzenia</h2>
<p>Budúce ceny sú odvodené z histórie, výmena trhov počas dňa a delenie výkonu medzi trhy sa nemodeluje,
pokles výnosu s kapacitou je lineárny (zjednodušenie – pri aFRR s výkonovým obmedzením je skutočný pokles menší).
Predvolené koeficienty degradácie (orientačne LFP), sadzby poplatkov, daňové pravidlá a ceny CAPEX/OPEX nie sú
overené údaje a treba ich prispôsobiť konkrétnemu projektu.</p>
</section></div></body></html>"""


def _fields():
    return [{"name": n, "default": d, "lo": lo, "hi": hi, "label": label, "unit": unit, "group": g}
            for n, d, lo, hi, _, label, unit, g in P.SPEC]


def _battery_text(p):
    preset = PR.BY_ID.get(p.get("preset") or "")
    prefix = "{} × {}, ".format(p.get("preset_units") or 1, preset["name"]) if preset else ""
    return prefix + "{} MW / {} MWh, {} cyklus/deň".format(num(p["power_mw"], 2), num(p["power_mw"] * p["duration_h"], 2),
                                                  num(p["cycles_per_day"], 1))


def _fig_html(fig, height=380, include_js=True):
    fig.update_layout(height=height, margin=dict(l=40, r=20, t=30, b=40), template="plotly_white",
                      legend=dict(orientation="h", y=-0.18), separators=", ")
    return fig.to_html(full_html=False, include_plotlyjs="cdn" if include_js else False,
                       config={"displayModeBar": False})


def _charts(s, daily, years):
    first = True

    def html(fig, height=380):
        nonlocal first
        out = _fig_html(fig, height, include_js=first)  # load plotly.js only once
        first = False
        return out

    y = years
    cf = go.Figure()
    cf.add_bar(x=y["year"], y=y["cf_project"], name="Peňažný tok projektu", marker_color="#0f4f8a")
    cf.add_scatter(x=y["year"], y=y["cf_project"].cumsum(), name="Kumulovaný – projekt", mode="lines+markers",
                   line=dict(color="#e76f51"))
    if s["params"]["debt_share_pct"] > 0:
        cf.add_scatter(x=y["year"], y=y["cf_equity"].cumsum(), name="Kumulovaný – vlastník",
                       mode="lines+markers", line=dict(color="#2a9d8f", dash="dot"))
    cf.update_layout(xaxis_title="rok", yaxis_title="€")

    op = y[y["year"] > 0]
    pl = go.Figure()
    pl.add_bar(x=op["year"], y=op["revenue_gross"], name="Výnosy", marker_color="#2a9d8f")
    for col, name, color in (("aggregator_fee", "Poplatok agregátorovi", "#e9c46a"),
                             ("grid_fees", "Sieťové poplatky", "#f4a261"), ("opex", "OPEX", "#e76f51"),
                             ("tax", "Daň", "#8d99ae"), ("interest", "Úroky", "#6d597a")):
        if op[col].abs().sum() > 0:
            pl.add_bar(x=op["year"], y=-op[col], name=name, marker_color=color)
    repl = op[op["capex"] > 0]
    if len(repl):
        pl.add_bar(x=repl["year"], y=-repl["capex"], name="Výmena článkov", marker_color="#b3261e")
    pl.add_scatter(x=op["year"], y=op["ebitda"], name="EBITDA", mode="lines+markers", line=dict(color="#083964"))
    pl.update_layout(barmode="relative", xaxis_title="rok", yaxis_title="€")

    soh = go.Figure()
    soh.add_scatter(x=op["year"], y=op["soh_start"] * 100, name="SoH na začiatku roka", mode="lines+markers",
                    line=dict(color="#0f4f8a"))
    soh.add_scatter(x=op["year"], y=op["soh_end"] * 100, name="SoH na konci roka", mode="lines+markers",
                    line=dict(color="#2a9d8f", dash="dot"))
    soh.add_hline(y=s["params"]["eol_soh_pct"], line_dash="dash", line_color="#b3261e",
                  annotation_text="koniec životnosti")
    soh.update_layout(xaxis_title="rok", yaxis_title="% pôvodnej kapacity", yaxis_range=[0, 105])

    hist = go.Figure()
    d = daily.copy()
    d["delivery_day"] = pd.to_datetime(d["delivery_day"])
    for market in P.MARKETS:
        part = d[d["market"] == market]
        if len(part):
            hist.add_bar(x=part["delivery_day"], y=part["revenue_eur"], name=MARKET_NAMES[market],
                         marker_color=COLORS[market])
    monthly = d.set_index("delivery_day")["revenue_eur"].resample("MS").mean()
    hist.add_scatter(x=monthly.index + pd.Timedelta(days=14), y=monthly.values, name="Mesačný priemer dňa",
                     mode="lines+markers", line=dict(color="#11243c"))
    hist.update_layout(barmode="stack", yaxis_title="€ / deň", bargap=0)

    sens = s["summary"]["sensitivity"]
    tornado = go.Figure()
    labels = [r["label"] for r in sens][::-1]
    base = sens[0]["base"] if sens else 0
    tornado.add_bar(y=labels, x=[r["low"] - base for r in sens][::-1], base=base, orientation="h",
                    name="nižšia hodnota", marker_color="#e76f51")
    tornado.add_bar(y=labels, x=[r["high"] - base for r in sens][::-1], base=base, orientation="h",
                    name="vyššia hodnota", marker_color="#2a9d8f")
    tornado.add_vline(x=base, line_color="#11243c")
    tornado.update_layout(barmode="overlay", xaxis_title="NPV projektu (€)")
    return html(cf), html(pl), html(soh, 320), html(hist), html(tornado, 340)


YEAR_TABLE = [("year", "Rok", 0), ("soh_start", "SoH začiatok %", 1), ("efc", "EFC", 0),
              ("discharged_mwh", "Vybitá MWh", 0), ("revenue_gross", "Výnosy", 0),
              ("aggregator_fee", "Agregátor", 0), ("grid_fees", "Sieť", 0), ("opex", "OPEX", 0),
              ("ebitda", "EBITDA", 0), ("capex", "Investícia", 0), ("depreciation", "Odpisy", 0),
              ("interest", "Úroky", 0), ("principal", "Splátka istiny", 0), ("tax", "Daň", 0),
              ("cf_project", "CF projekt", 0), ("cf_equity", "CF vlastník", 0), ("dscr", "DSCR", 2)]


@bp.route("/kalkulacka", methods=["GET", "POST"])
def form():
    conn = _conn()
    try:
        data_start, data_end = simulation.data_range(conn)
        error = None
        values = dict(P.DEFAULTS, strategy="DA", preset="", preset_units=1)
        if request.method == "POST":
            raw = request.form.to_dict()
            values.update(raw)
            try:
                sid = scenario.create(conn, raw)
            except (P.ParamError, scenario.Busy) as exc:
                error = str(exc)
            else:
                scenario.launch(sid)
                return redirect(url_for("calculator.result", scenario_id=sid))
        elif request.args.get("from", "").isdigit():
            s, _, _ = scenario.load(conn, int(request.args["from"]))
            if s:
                values.update(s["params"])
        if not values.get("period_start") and data_end is not None:
            values["period_start"] = max(data_start, data_end - pd.Timedelta(days=364).to_pytimedelta()).isoformat()
            values["period_end"] = data_end.isoformat()
        with conn.cursor() as cur:
            cur.execute("""SELECT scenario_id, created_at, status, params, summary FROM calc_scenario
                           ORDER BY scenario_id DESC LIMIT 10""")
            rows = cur.fetchall()
        conn.commit()
        recent = []
        for sid, created, status, p, summary in rows:
            kpi = (summary or {}).get("kpi", {})
            recent.append({
                "id": sid,
                "created": pd.Timestamp(created).tz_convert("Europe/Bratislava").strftime("%d. %m. %Y %H:%M"),
                "battery": _battery_text(p), "strategy": P.STRATEGY_LABELS.get(p["strategy"], p["strategy"]),
                "status": {"success": "hotovo", "failed": "chyba", "running": "počíta sa",
                           "queued": "čaká"}.get(status, status),
                "npv": num(kpi.get("npv_project")) + " €" if kpi else "–",
                "irr": pct(kpi.get("irr_project")) if kpi else "–",
            })
        return render_template_string(
            FORM, style=STYLE, error=error, values=values, fields=_fields(), groups=P.GROUPS,
            strategies=list(P.STRATEGY_LABELS.items()), data_start=data_start, data_end=data_end, recent=recent,
            presets=[PR.describe(b) for b in PR.PRESETS], price_source=PR.PRICE_SOURCE)
    finally:
        conn.close()


@bp.route("/kalkulacka/metodika")
def method():
    return render_template_string(METHOD, style=STYLE, eur_per_usd=PR.EUR_PER_USD, pcs=int(PR.PCS_EUR_KW),
                                  inst=int(PR.INSTALLATION_PCT))


@bp.route("/kalkulacka/<int:scenario_id>")
def result(scenario_id):
    conn = _conn()
    try:
        s, daily, years = scenario.load(conn, scenario_id)
    finally:
        conn.close()
    if s is None:
        abort(404)
    edit_url = url_for("calculator.form") + "?from={}".format(scenario_id)
    if s["status"] != "success":
        return render_template_string(PROGRESS, style=STYLE, s=s, edit_url=edit_url)
    p, summary, k = s["params"], s["summary"], s["summary"]["kpi"]
    sign = lambda v: "" if v is None else ("pos" if v >= 0 else "neg")  # noqa: E731
    kpis = [
        ("CAPEX", num(k["capex_eur"]) + " €", ""),
        ("Výnosy v 1. roku", num(k["annual_revenue_year1"]) + " €", ""),
        ("NPV projektu", num(k["npv_project"]) + " €", sign(k["npv_project"])),
        ("IRR projektu", pct(k["irr_project"]), sign(k["irr_project"])),
        ("Doba návratnosti", years_text(k["payback_years"]), ""),
        ("Diskontovaná návratnosť", years_text(k["payback_discounted_years"]), ""),
        ("LCOS", (num(k["lcos_eur_mwh"]) + " €/MWh") if k["lcos_eur_mwh"] else "–", ""),
        ("SoH na konci", pct(k["soh_end"], 0), ""),
    ]
    if k["debt_eur"] > 0:
        kpis += [("Úver", num(k["debt_eur"]) + " €", ""),
                 ("NPV vlastníka", num(k["npv_equity"]) + " €", sign(k["npv_equity"])),
                 ("IRR vlastníka", pct(k["irr_equity"]), sign(k["irr_equity"])),
                 ("Min. DSCR", num(k["min_dscr"], 2), "neg" if (k["min_dscr"] or 0) < 1.2 else "pos")]
    if k["replacement_years"]:
        kpis.append(("Výmena článkov v roku", ", ".join(str(y) for y in k["replacement_years"]), ""))

    year_rows = []
    for r in years.itertuples(index=False):
        row = []
        for col, _, digits in YEAR_TABLE:
            v = getattr(r, col)
            if col == "soh_start":
                v = None if r.year == 0 else v * 100
            row.append(num(v, digits) if col != "year" else int(v))
        year_rows.append(row)
    preset = PR.BY_ID.get(p.get("preset") or "")
    assumptions = [("Typ batérie", "{} × {} ({}; cena {}, {})".format(
                        p.get("preset_units") or 1, preset["name"], PR.PRICE_SOURCE,
                        PR.describe(preset)["price"], preset["price_note"]) if preset else "vlastná konfigurácia"),
                   ("Stratégia trhu", P.STRATEGY_LABELS.get(p["strategy"])),
                   ("Obdobie historických cien", "{} – {}".format(p["period_start"], p["period_end"])),
                   ("Ročný výnos z histórie (plná kapacita)", num(summary["annual_revenue_eur"]) + " €"),
                   ("EFC za rok zo simulácie", num(summary["annual_efc"], 0))]
    p = dict(P.DEFAULTS, **p)  # scenarios stored before a parameter existed use its default
    assumptions += [("{} ({})".format(label, unit),
                     ("áno" if p[name] else "nie") if name == "replace_at_eol"
                     else num(p[name], 2 if kind is float else 0))
                    for name, _, _, _, kind, label, unit, _ in P.SPEC]
    charts = _charts(s, daily, years)
    return render_template_string(
        RESULT, style=STYLE, s=s, params=p, edit_url=edit_url, summary=summary, kpis=kpis, battery=_battery_text(p),
        strategy=P.STRATEGY_LABELS.get(p["strategy"]), names=MARKET_NAMES, num=num,
        chart_cf=charts[0], chart_pl=charts[1], chart_soh=charts[2], chart_hist=charts[3],
        chart_tornado=charts[4], year_headers=[h for _, h, _ in YEAR_TABLE], year_rows=year_rows,
        assumptions=assumptions)


@bp.route("/kalkulacka/<int:scenario_id>.csv")
def csv(scenario_id):
    conn = _conn()
    try:
        s, _, years = scenario.load(conn, scenario_id)
    finally:
        conn.close()
    if s is None or s["status"] != "success":
        abort(404)
    buf = io.StringIO()
    buf.write("# Kalkulačka batérie, výpočet {}\n".format(scenario_id))
    for name, value in s["params"].items():
        buf.write("# {} = {}\n".format(name, value))
    years.to_csv(buf, index=False, sep=";", decimal=",")
    return Response(buf.getvalue().encode("utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=kalkulacka_{}.csv".format(scenario_id)})
