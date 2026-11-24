"""Battery modelling (state of health) and investment analysis, year by year.

Pure functions without database access. Conventions:
  * year 0 = investment, years 1..N = operation, cash flows at the end of each year;
  * revenue of year y = annual historical revenue x realization factor x (1 + growth)^(y-1)
    x average SoH of the year (usable capacity shrinks with degradation);
  * SoH falls by a calendar part (% per year) and a cycle part (% per equivalent full cycle);
    at the end-of-life SoH the cells are replaced (optional, only with at least 3 more years of
    operation) and SoH returns to 100 %;
  * tax: straight-line depreciation, losses carried forward without limit, interest is deductible;
  * project cash flow (unlevered) ignores financing, equity cash flow includes the loan (annuity).
"""

MIN_YEARS_AFTER_REPLACEMENT = 3  # no cell replacement in the last years of the project


def annuity_payment(principal, rate, years):
    if principal <= 0 or years <= 0:
        return 0.0
    if rate == 0:
        return principal / years
    return principal * rate / (1 - (1 + rate) ** -years)


def npv(rate, cash_flows):
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(cash_flows))


def irr(cash_flows, tol=1e-9):
    """Internal rate of return; None if there is none.

    With several sign changes (e.g. a cell replacement late in the project) NPV(r) can have more
    roots; the economically meaningful one is the highest rate where NPV falls from positive to
    negative, so the rate axis is scanned and that crossing is refined by bisection.
    """
    grid = [-0.99 + 0.01 * i for i in range(100)] + [0.01 * i for i in range(1, 200)] +         [2 + 0.25 * i for i in range(33)]
    values = [npv(r, cash_flows) for r in grid]
    best = None
    for (lo, f_lo), (hi, f_hi) in zip(zip(grid, values), zip(grid[1:], values[1:])):
        if f_lo == 0:
            best = lo
        elif f_lo > 0 > f_hi:
            for _ in range(100):
                mid = (lo + hi) / 2
                f_mid = npv(mid, cash_flows)
                if f_mid > 0:
                    lo = mid
                else:
                    hi = mid
                if hi - lo < tol:
                    break
            best = (lo + hi) / 2
    return best


def payback(cash_flows, rate=None):
    """Years until the cumulative (optionally discounted) cash flow turns positive; None if never."""
    cumulative = 0.0
    for t, cf in enumerate(cash_flows):
        value = cf / (1 + rate) ** t if rate is not None else cf
        if t > 0 and cumulative < 0 <= cumulative + value and value > 0:
            return t - 1 + (-cumulative) / value
        cumulative += value
    return None


def capex(p):
    e_kwh = p["power_mw"] * p["duration_h"] * 1000
    energy = p["capex_energy_eur_kwh"] * e_kwh
    equipment = energy + p["capex_power_eur_kw"] * p["power_mw"] * 1000
    total = equipment * (1 + p.get("capex_installation_pct", 0.0) / 100) + p["capex_fixed_eur"]
    return total, energy


def project(p, annual_revenue, annual_efc):
    """Year-by-year table and KPIs.

    p: validated parameters (bri.calculator.params.validate);
    annual_revenue: EUR per year of the full-capacity battery from the historical simulation;
    annual_efc: equivalent full cycles per year from the simulation.
    """
    life = p["project_life_years"]
    capex_total, capex_energy = capex(p)
    e_mwh = p["power_mw"] * p["duration_h"]
    rate = p["discount_rate_pct"] / 100
    tax_rate = p["tax_rate_pct"] / 100
    debt = capex_total * p["debt_share_pct"] / 100
    tenor = min(p["loan_tenor_years"], life)
    interest_rate = p["interest_rate_pct"] / 100
    payment = annuity_payment(debt, interest_rate, tenor)
    eol = p["eol_soh_pct"] / 100
    cal = p["calendar_fade_pct"] / 100
    cyc = p["cycle_fade_pct_per_100efc"] / 100 / 100

    rows = [{
        "year": 0, "soh_start": 1.0, "soh_end": 1.0, "efc": 0.0, "discharged_mwh": 0.0,
        "revenue_gross": 0.0, "aggregator_fee": 0.0, "grid_fees": 0.0, "opex": 0.0, "ebitda": 0.0,
        "capex": capex_total, "depreciation": 0.0, "interest": 0.0, "principal": 0.0, "tax": 0.0,
        "cf_project": -capex_total, "cf_equity": -(capex_total - debt), "dscr": None,
    }]
    assets = [(1, capex_total)]  # (first depreciation year, amount)
    balance = debt
    soh = 1.0
    loss_project = loss_equity = 0.0  # tax losses carried forward
    replacements = []

    for y in range(1, life + 1):
        soh_start = soh
        soh_end = max(0.0, soh_start - cal - cyc * annual_efc)
        soh_avg = (soh_start + soh_end) / 2
        revenue = (annual_revenue * p["revenue_factor_pct"] / 100
                   * (1 + p["revenue_growth_pct"] / 100) ** (y - 1) * soh_avg)
        fee = revenue * p["aggregator_fee_pct"] / 100
        discharged = annual_efc * e_mwh * soh_avg
        charged = discharged / (p["efficiency"] ** 2) if p["efficiency"] else 0.0
        grid = charged * p["grid_fee_eur_mwh"]
        opex = capex_total * p["opex_pct_capex"] / 100 * (1 + p["opex_escalation_pct"] / 100) ** (y - 1)
        ebitda = revenue - fee - grid - opex

        invest = 0.0
        if p["replace_at_eol"] and soh_end <= eol and y <= life - MIN_YEARS_AFTER_REPLACEMENT:
            invest = capex_energy * p["replacement_cost_pct"] / 100
            assets.append((y + 1, invest))
            replacements.append(y)
            soh = 1.0
        else:
            soh = soh_end
        if y == life:
            invest -= capex_total * p["residual_value_pct"] / 100

        depreciation = 0.0
        for first, amount in assets:
            years = min(p["depreciation_years"], max(1, life - first + 1))
            if first <= y < first + years:
                depreciation += amount / years

        interest = balance * interest_rate if y <= tenor else 0.0
        principal = (payment - interest) if y <= tenor else 0.0
        balance -= principal

        taxable_project = ebitda - depreciation - loss_project
        tax_project = max(0.0, taxable_project) * tax_rate
        loss_project = max(0.0, -taxable_project)
        taxable_equity = ebitda - depreciation - interest - loss_equity
        tax_equity = max(0.0, taxable_equity) * tax_rate
        loss_equity = max(0.0, -taxable_equity)

        debt_service = interest + principal
        rows.append({
            "year": y, "soh_start": soh_start, "soh_end": soh_end, "efc": annual_efc,
            "discharged_mwh": discharged, "revenue_gross": revenue, "aggregator_fee": fee,
            "grid_fees": grid, "opex": opex, "ebitda": ebitda, "capex": invest,
            "depreciation": depreciation, "interest": interest, "principal": principal, "tax": tax_equity,
            "cf_project": ebitda - tax_project - invest,
            "cf_equity": ebitda - debt_service - tax_equity - invest,
            "dscr": (ebitda - tax_equity) / debt_service if debt_service > 0 else None,
        })

    cf_p = [r["cf_project"] for r in rows]
    cf_e = [r["cf_equity"] for r in rows]
    pv_costs = capex_total + sum(
        (r["opex"] + r["grid_fees"] + (r["capex"] if r["year"] > 0 else 0.0)) / (1 + rate) ** r["year"]
        for r in rows[1:])
    pv_energy = sum(r["discharged_mwh"] / (1 + rate) ** r["year"] for r in rows[1:])
    dscrs = [r["dscr"] for r in rows if r["dscr"] is not None]
    kpi = {
        "capex_eur": capex_total,
        "debt_eur": debt,
        "equity_eur": capex_total - debt,
        "annual_revenue_year1": rows[1]["revenue_gross"] if life else 0.0,
        "npv_project": npv(rate, cf_p),
        "npv_equity": npv(rate, cf_e),
        "irr_project": irr(cf_p),
        "irr_equity": irr(cf_e) if debt > 0 else irr(cf_p),
        "payback_years": payback(cf_p),
        "payback_discounted_years": payback(cf_p, rate),
        "lcos_eur_mwh": pv_costs / pv_energy if pv_energy > 0 else None,
        "min_dscr": min(dscrs) if dscrs else None,
        "avg_dscr": sum(dscrs) / len(dscrs) if dscrs else None,
        "replacement_years": replacements,
        "soh_end": rows[-1]["soh_end"],
    }
    return rows, kpi


SENSITIVITY = [
    # (key, Slovak label, how the parameter / input is changed for the low and high case)
    ("revenue", "Výnosy ±20 %", ("revenue", 0.8), ("revenue", 1.2)),
    ("capex", "CAPEX ±20 %", ("capex", 0.8), ("capex", 1.2)),
    ("opex", "OPEX ±30 %", ("opex_pct_capex", 0.7), ("opex_pct_capex", 1.3)),
    ("degradation", "Degradácia ±50 %", ("degradation", 0.5), ("degradation", 1.5)),
    ("discount", "Diskontná sadzba ±2 p. b.", ("discount_rate_pct", -2), ("discount_rate_pct", 2)),
    ("fee", "Poplatok agregátorovi ±5 p. b.", ("aggregator_fee_pct", -5), ("aggregator_fee_pct", 5)),
]


def _apply(p, revenue, change):
    what, value = change
    q = dict(p)
    if what == "revenue":
        return q, revenue * value
    if what == "capex":
        for k in ("capex_energy_eur_kwh", "capex_power_eur_kw", "capex_fixed_eur"):  # installation % follows
            q[k] = p[k] * value
    elif what == "degradation":
        q["calendar_fade_pct"] = p["calendar_fade_pct"] * value
        q["cycle_fade_pct_per_100efc"] = p["cycle_fade_pct_per_100efc"] * value
    elif what == "opex_pct_capex":
        q[what] = p[what] * value
    else:  # percentage points
        q[what] = max(0.0, p[what] + value)
    return q, revenue


def sensitivity(p, annual_revenue, annual_efc):
    """Project NPV for the low and high value of each driver (tornado chart)."""
    _, base = project(p, annual_revenue, annual_efc)
    out = []
    for key, label, low, high in SENSITIVITY:
        values = []
        for change in (low, high):
            q, rev = _apply(p, annual_revenue, change)
            values.append(project(q, rev, annual_efc)[1]["npv_project"])
        out.append({"key": key, "label": label, "low": values[0], "high": values[1],
                    "base": base["npv_project"]})
    out.sort(key=lambda r: abs(r["high"] - r["low"]), reverse=True)
    return out
