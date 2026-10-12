"""How close is our value to what homes actually sold for?

Every home that sold in the last 90 days is valued as of the day before it sold,
using only sales before that day, by each method, then compared with its real
sale price.

    .venv/bin/python scripts/backtest.py            # writes data/backtest.json, prints the tables
    .venv/bin/python scripts/backtest.py --quick    # every 5th holdout home

The site's accuracy line is read from data/backtest.json, never typed.
"""
import json, math, sys, statistics, collections, datetime as dt
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from msp import valuation as V

D = Path(__file__).resolve().parent.parent / "data"
TODAY = dt.date.today()
HOLDOUT_DAYS = 90
TYPES = ("house", "townhome", "condo")
BANDS = [(0, 200e3, "under $200k"), (200e3, 300e3, "$200-300k"), (300e3, 450e3, "$300-450k"), (450e3, 700e3, "$450-700k"), (700e3, 1e12, "$700k+")]


def stats(errs):
    a = np.abs(errs)
    return {"n": len(errs), "median_abs_pct": round(float(np.median(a)) * 100, 1) if len(a) else None,
            "within_5": round(float(np.mean(a <= .05)) * 100, 1) if len(a) else None,
            "within_10": round(float(np.mean(a <= .10)) * 100, 1) if len(a) else None,
            "within_20": round(float(np.mean(a <= .20)) * 100, 1) if len(a) else None,
            "bias_pct": round(float(np.median(errs)) * 100, 1) if len(a) else None}


def window():
    """The holdout: the last 90 days. --validate instead scores the 90 days before
    that, which is where the method's settings were tuned, so the holdout stays unseen."""
    if "--validate" in sys.argv:
        return (TODAY - dt.timedelta(days=2 * HOLDOUT_DAYS)).isoformat(), (TODAY - dt.timedelta(days=HOLDOUT_DAYS)).isoformat()
    return (TODAY - dt.timedelta(days=HOLDOUT_DAYS)).isoformat(), "9999"


def subjects(pool, every=1):
    lo, hi = window()
    out = []
    for i, r in enumerate(pool.rows):
        if lo <= r["date"] < hi and r["type_group"] in TYPES:
            out.append(i)
    return out[::every]


def subject_of(pool, i):
    r = pool.rows[i]
    return dict(r, x=pool.x[i], y=pool.y[i], year=r["year"])


PIN_LEN = {"hennepin": 13, "washington": 13, "ramsey": 12, "dakota": 12, "anoka": 12, "scott": 9, "carver": 9}
BRICKS = Path.home() / "Desktop/bricks/data"


def county_opinions(pool, subs):
    """The index's other two opinions for each holdout home, as build_index.py computes
    them, from records dated before the sale: county EMV × the local sale/EMV ratio
    (recorded sales before the sale date), and the last deed (2+ years old) indexed."""
    import duckdb
    rows = []
    for i in subs:
        r = pool.rows[i]
        apn = "".join(ch for ch in (r.get("apn") or "") if ch.isdigit())
        if apn:
            rows.append((i, r["county"], apn.zfill(PIN_LEN.get(r["county"], len(apn))), r["date"]))
    con = duckdb.connect()
    con.execute("install spatial; load spatial")
    con.execute(f"attach '{D / 'county.duckdb'}' as cty (read_only)")
    con.execute(f"attach '{BRICKS / 'closed_system.duckdb'}' as cs (read_only)")
    con.execute("create table H (i int, county varchar, pin varchar, sold date)")
    if not rows:
        return {}
    con.executemany("insert into H values (?, ?, ?, ?)", rows)
    con.execute("""create table J as select h.*, p.emv_total as emv, p.is_condo, ST_X(ST_Centroid(p.geom)) x, ST_Y(ST_Centroid(p.geom)) y,
                   p.sale_date as deed_date, p.sale_value as deed_price
                   from H h join cty.parcels p on p.county = h.county and p.pin = h.pin where p.emv_total > 0""")
    cal = dict(con.execute("""
    with s as (select *, ST_X(ST_Centroid(geom)) sx, ST_Y(ST_Centroid(geom)) sy from cty.sales),
    pairs as (select j.i, s.ratio, sqrt((j.x - s.sx)^2 + (j.y - s.sy)^2) as dist from J j join s
              on s.sx between j.x - 8000 and j.x + 8000 and s.sy between j.y - 8000 and j.y + 8000
              and s.emv between j.emv / 1.35 and j.emv * 1.35 and s.sale_date < j.sold and not (s.county = j.county and s.pin = j.pin)),
    near as (select * from pairs qualify row_number() over (partition by i order by dist) <= 30)
    select i, case when count(*) >= 10 then median(ratio) end from near group by 1""").fetchall())
    con.execute("""create table idx as select p.county, p.is_condo, year(s.sale_date) as yr, median(s.sale_value / p.emv_total) as r
    from cs.sale s join cty.parcels p on p.parcel_key = s.parcel_key
    where p.is_dwelling and p.emv_total > 50000 and s.sale_value > 30000 and year(s.sale_date) between 2005 and 2026
      and s.sale_value / p.emv_total between 0.2 and 3 group by all having count(*) >= 200""")
    con.execute("create table idx_now as select county, is_condo, median(ratio) r from cty.sales group by all")
    out = {}
    for i, emv, deed_date, deed_price, then, now, sold in con.execute("""select j.i, j.emv, j.deed_date, j.deed_price, a.r, b.r, j.sold from J j
            left join idx a on a.county = j.county and a.is_condo = j.is_condo and a.yr = year(j.deed_date)
            left join idx_now b on b.county = j.county and b.is_condo = j.is_condo""").fetchall():
        e = {"emv": emv}
        if cal.get(i):
            e["cal"] = emv * cal[i]
        if deed_price and deed_date and then and now and deed_date.year >= 2005 and (sold - deed_date).days > 730 and deed_price >= 50000:
            d = deed_price * now / then
            if 0.6 <= d / (emv * now) <= 1.6:
                e["deed"] = d
        out[i] = e
    return out


def report(results, methods, coverage_of):
    """results: list of (row, {method: estimate}, ...)."""
    out = {}
    for m in methods:
        e = [(x[0], (x[1][m] - x[0]["price"]) / x[0]["price"]) for x in results if x[1].get(m)]
        errs = np.array([x for _, x in e])
        block = {"all": stats(errs), "coverage_pct": round(100 * len(e) / max(1, coverage_of), 1)}
        for key, f in (("county", lambda r: r["county"]), ("type", lambda r: r["type_group"]),
                       ("band", lambda r: next(b[2] for b in BANDS if b[0] <= r["price"] < b[1]))):
            g = collections.defaultdict(list)
            for r, x in e:
                g[f(r)].append(x)
            block[key] = {k: stats(np.array(v)) for k, v in sorted(g.items())}
        out[m] = block
    return out


def print_report(rep):
    for m, b in rep.items():
        a = b["all"]
        print(f"\n== {m}: n={a['n']} (coverage {b['coverage_pct']}%)  median |err| {a['median_abs_pct']}%  "
              f"within 5/10/20%: {a['within_5']}/{a['within_10']}/{a['within_20']}  bias {a['bias_pct']:+}%")
        for key in ("county", "type", "band"):
            for k, s in b[key].items():
                print(f"   {key:6} {k:12} n={s['n']:5}  med|err| {s['median_abs_pct']:5}%  w10 {s['within_10']:5}%  bias {s['bias_pct']:+5}%")


def tiers():
    """build_index.py's condition tier from listing words and its as-is ratios, so the
    backtest values a sold home exactly as the index values a listing."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_index", Path(__file__).resolve().parent / "build_index.py")
    bi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bi)
    cond = json.loads((D / "condition.json").read_text())

    def tier(text):
        for t in ("condemned", "heavy", "light"):
            if bi.COND_TXT[t].search(text or ""):
                return t, cond["tiers"][t]["ratio"]
        return "none", 1.0
    return tier


NO_CAL = True  # the validate-window recalibration made it worse (bias +0.9%); left off
TRAIN_FROM = "2025-12-01"  # two months after the pool starts, so the first fits have comps
TEST_HOUSES = {"7744511998": "8031 Tierneys Woods Curv, Bloomington: asking $499,900, the index said $921k (2026-10-11)"}


def main():
    every = 5 if "--quick" in sys.argv else 1
    for a in sys.argv:  # --set=NAME=value overrides a setting in msp/valuation.py, for tuning on --validate
        if a.startswith("--set="):
            k, v = a[6:].split("=")
            setattr(V, k, type(getattr(V, k))(v))
    pool = V.Pool()
    V.attach_emv(pool)
    tier = tiers()
    hold_lo = (TODAY - dt.timedelta(days=HOLDOUT_DAYS)).isoformat()
    val_lo = (TODAY - dt.timedelta(days=2 * HOLDOUT_DAYS)).isoformat()
    # Each home is valued with area models fitted on sales before its month (Models),
    # comps sold before its day (grid), and condition phrases measured before the holdout.
    # Every sale from TRAIN_FROM is valued too: the sale-to-list model trains on the ones
    # before the holdout (all seasons). Each still uses only models fitted before its month.
    models = V.Models(pool, holdout_from=TRAIN_FROM)
    subs = [i for i, r in enumerate(pool.rows) if r["date"] >= TRAIN_FROM and r["type_group"] in TYPES][::every]
    print(f"pool {pool.n} sales; valuing {len(subs)} sold since {val_lo} (validate < {hold_lo} <= holdout)", flush=True)
    county = county_opinions(pool, subs)
    print(f"matched to a county parcel: {len(county)}", flush=True)
    results = []
    for k, i in enumerate(subs):
        s = subject_of(pool, i)
        r = pool.rows[i]
        c = county.get(i) or {}
        est = {"baseline": V.baseline(s, pool, s["date"])}
        g = V.grid(s, pool, s["date"], models)
        est["grid"] = g["value"] if g else None
        est["emv_comps"] = g.get("emv_value") if g else None
        est["comp_new"] = V.reconcile(g) if g else est["baseline"]
        t, ratio = tier(r.get("text"))
        others = [c.get("cal"), c.get("deed")]
        ce = (r.get("cond") or (0.0, []))[0]
        others_new = [x * (1 + ce) if x and ce < 0 else x for x in others]  # as build_index.py: county and deed can't see inside
        for name, comp, oth in (("worth_shipped", est["baseline"], others), ("worth_new", est["comp_new"], others_new)):
            three = [x for x in [comp] + oth if x]
            est[name] = float(np.median(three)) * ratio if three else None
        results.append((r, est, {"ask": r.get("list_price"), "tier": t, "cond": r.get("cond"), "grid": g, "i": i}))
        if k % 4000 == 0:
            print(f"  {k}/{len(subs)}", flush=True)
    # The asking price is information: fit on the validate window, score on the holdout.
    fit_rows = [(e["worth_new"], x["ask"], r["price"], x["cond"]) for r, e, x in results if val_lo <= r["date"] < hold_lo and e["worth_new"] and x["ask"]]
    ask = V.AskModel.fit(fit_rows)
    for r, e, x in results:
        e["expected"] = ask.predict(e["worth_new"], x["ask"], x["cond"]) if e["worth_new"] and x["ask"] else e["worth_new"]
    methods = ["baseline", "grid", "emv_comps", "comp_new", "worth_shipped", "worth_new", "expected"]
    hold = [(r, e) for r, e, x in results if r["date"] >= hold_lo]
    val = [(r, e) for r, e, x in results if val_lo <= r["date"] < hold_lo]
    which = val if "--validate" in sys.argv else hold
    rep = report(which, methods, len(which))
    print_report(rep)
    # Homes whose ask was 25%+ under the shipped worth: where a wrong value does the most harm.
    deep = [(r, e) for r, e, x in results if r["date"] >= hold_lo and x["ask"] and e["worth_shipped"] and x["ask"] < 0.75 * e["worth_shipped"]]
    deep_rep = report(deep, ["worth_shipped", "worth_new", "expected"], len(deep))
    print(f"\n## asked 25%+ under the shipped worth: {len(deep)} holdout homes")
    for m in ("worth_shipped", "worth_new", "expected"):
        a = deep_rep[m]["all"]
        print(f"  {m:14} med|err| {a['median_abs_pct']}%  w10 {a['within_10']}%  bias {a['bias_pct']:+}%")
    print("\n## ask curve (fit on the validate window)")
    for c in ask.table:
        print(f"  ask {c['ask_vs_estimate']:+.0%} vs estimate: n={c['n']:5}  sale vs estimate fit {c['sale_vs_estimate_fit']:+.1%}, "
              f"observed {c['sale_vs_estimate_observed_median'] if c['sale_vs_estimate_observed_median'] is None else format(c['sale_vs_estimate_observed_median'], '+.1%')}; "
              f"sale vs ask {c['sale_vs_ask_fit']:+.1%}")
    sl = sale_list(results, hold_lo)
    tests = named_tests(pool, models, ask, tier)
    for pid, t in tests.items():
        print(f"\n## test {pid}: {t['note']}\n  baseline comps {t['baseline_comp']}, grid {t['grid']}, condition {t['condition']}, tier {t['tier']}, below_share {t['below_share']}")
        for c in t["comps"]:
            print(f"    {c}")
    out = {"built": TODAY.isoformat(), "holdout_days": HOLDOUT_DAYS, "holdout_from": hold_lo, "validate_from": val_lo,
           "holdout_n": len(hold), "validate_n": len(val), "sale_list_train_from": TRAIN_FROM, "sample_every": every, "pool_n": pool.n,
           "methods": rep, "deep_discount": {"n": len(deep), "methods": deep_rep}, "ask_model": ask.summary(), "sale_list": sl, "tests": tests,
           "settings": V.Models.settings()}
    if every == 1 and "--validate" not in sys.argv and not any(a.startswith("--set=") for a in sys.argv):
        (D / "backtest.json").write_text(json.dumps(out, indent=1, default=str))
        # Production refits the ask curve on the whole 180 days.
        allfit = [(e["worth_new"], x["ask"], r["price"], x["cond"]) for r, e, x in results if r["date"] >= val_lo and e["worth_new"] and x["ask"]]
        (D / "ask_model.json").write_text(json.dumps(V.AskModel.fit(allfit).summary(), indent=1))
    return out


def recent_shift(m, rows, before, days=60):
    lo = (dt.date.fromisoformat(before) - dt.timedelta(days=days)).isoformat()
    r = [math.log(p / f["ask"]) - m.raw(f) for f, p, d in rows if lo <= d < before]
    return round(float(np.median(r)), 4) if r else 0.0


def sale_list(results, hold_lo):
    """Room to negotiate: predict sale ÷ last ask from what the listing showed while for sale.
    Fit on sales before the holdout, scored on the holdout; production refit on all."""
    rows = []
    for r, e, x in results:
        run = r.get("run")
        if not run or not e.get("worth_new"):
            continue
        dom = (dt.date.fromisoformat(r["date"][:10]) - dt.date.fromisoformat(run["listed"])).days - V.CLOSE_LAG
        month = (dt.date.fromisoformat(r["date"][:10]) - dt.timedelta(days=V.CLOSE_LAG)).month
        f = V.sl_inputs(run["ask"], e["worth_new"], max(dom, 0), run["n_cuts"], run["orig"], run["relisted"], r["type_group"],
                        V.area_of(r), month, (r.get("cond") or (0, []))[0], r.get("hoa"))
        rows.append((f, r["price"], r["date"] >= hold_lo, r["date"]))
    val_lo = (TODAY - dt.timedelta(days=2 * HOLDOUT_DAYS)).isoformat()
    train = [(f, p) for f, p, h, d in rows if not h]
    test = [(f, p) for f, p, h, d in rows if h]
    # Shrink: the regression is fitted on sales before the validate window and its predictions
    # scaled by the factor that minimises median error on the validate window (it overreaches
    # out of sample otherwise); then refitted on everything before the holdout.
    m1 = V.SaleListModel.fit([(f, p) for f, p, h, d in rows if d < val_lo])
    vrows = [(f, p) for f, p, h, d in rows if val_lo <= d and not h]
    # Calibration: least squares of the validate window's actual log(sale/ask) on the raw
    # prediction, so a predicted 3% under the ask means 3% on average.
    xr = np.array([m1.raw(f) for f, _ in vrows])
    yr = np.array([math.log(p / f["ask"]) for f, p in vrows])
    slope, shift = np.polyfit(xr, yr, 1)
    best = (round(float(slope), 3), round(float(shift), 4))
    m = V.SaleListModel.fit(train)
    m.shrink, m.shift = (1.0, 0.0) if NO_CAL else best
    # Market temperature: the median miss over the last 60 days of training, so a cooling
    # (or heating) market moves the level. Only sales before the holdout are read.
    m.shift = recent_shift(m, [(f, p, d) for f, p, h, d in rows if not h], hold_lo)
    print(f"  market-temperature shift from the 60 days before {hold_lo}: {m.shift:+.4f}")
    print(f"  calibration on the validate window: slope {best[0]}, shift {best[1]}")
    pred = np.array([f["ask"] * m.ratio(f) for f, _ in test])
    act = np.array([p for _, p in test])
    asks = np.array([f["ask"] for f, _ in test])
    err = (pred - act) / act
    err_ask = (asks - act) / act
    st = lambda e: {"n": len(e), "median_abs_pct": round(float(np.median(np.abs(e))) * 100, 2), "within_2": round(float(np.mean(np.abs(e) <= .02)) * 100, 1),
                    "within_5": round(float(np.mean(np.abs(e) <= .05)) * 100, 1), "within_10": round(float(np.mean(np.abs(e) <= .10)) * 100, 1),
                    "bias_pct": round(float(np.median(e)) * 100, 2)}
    pr, ar = pred / asks, act / asks
    order = np.argsort(pr)
    dec = []
    for k in range(10):
        idx = order[k * len(order) // 10:(k + 1) * len(order) // 10]
        dec.append({"decile": k + 1, "n": int(len(idx)), "predicted_sale_vs_ask": round(float(np.mean(pr[idx])) - 1, 4),
                    "actual_sale_vs_ask": round(float(np.mean(ar[idx])) - 1, 4)})
    # Long-listed, cut homes: where room to negotiate should exist.
    soft = np.array([f["dom"] >= 91 or f["n_cuts"] >= 2 for f, _ in test])
    # Ranking power: do the homes we'd put at the top actually sell further under their ask?
    top = pr <= np.quantile(pr, 0.05)
    out = {"train_n": len(train), "holdout_n": len(test), "predicted": st(err), "ask_alone": st(err_ask),
           "calibration": {"slope": best[0], "shift": best[1]}, "top_5pct_room": {"n": int(top.sum()), "predicted_sale_vs_ask": round(float(np.median(pr[top])) - 1, 4),
                                                   "actual_sale_vs_ask": round(float(np.median(ar[top])) - 1, 4)},
           "soft_listings": {"n": int(soft.sum()), "predicted": st(err[soft]), "ask_alone": st(err_ask[soft])}, "deciles": dec,
           "model": m.summary()}
    print(f"\n## sale-to-list model: train {len(train):,}, holdout {len(test):,}")
    print(f"  predicted sale: {out['predicted']}\n  ask alone:      {out['ask_alone']}")
    print(f"  most room (predicted bottom 5%, n={out['top_5pct_room']['n']}): predicted {out['top_5pct_room']['predicted_sale_vs_ask']:+.1%}, actual {out['top_5pct_room']['actual_sale_vs_ask']:+.1%}")
    print(f"  long-listed or 2+ cuts ({out['soft_listings']['n']}): predicted {out['soft_listings']['predicted']}  ask alone {out['soft_listings']['ask_alone']}")
    for d_ in dec:
        print(f"  decile {d_['decile']:2}: n={d_['n']:5} predicted {d_['predicted_sale_vs_ask']:+.1%}  actual {d_['actual_sale_vs_ask']:+.1%}")
    for c, v in out["model"]["as_percent"].items():
        print(f"    {v:+.1%}  {c}")
    if "--validate" not in sys.argv:
        mf = V.SaleListModel.fit([(f, p) for f, p, _, _ in rows])
        mf.shrink, mf.shift = (1.0, 0.0) if NO_CAL else best
        mf.shift = recent_shift(mf, [(f, p, d) for f, p, _, d in rows], TODAY.isoformat())
        (D / "sale_list_model.json").write_text(json.dumps(mf.summary(), indent=1))
    return out


def named_tests(pool, models, ask, tier):
    """Listings a person flagged, valued today, old way and new."""
    import importlib.util
    out = {}
    lst = {}
    for line in open(D / "listings_raw.jsonl"):
        r = json.loads(line)
        if r["property_id"] in TEST_HOUSES:
            lst[r["property_id"]] = r
    details = V.load_details()
    for pid, note in TEST_HOUSES.items():
        L = lst.get(pid)
        if not L:
            continue
        d = L["description"]
        a = L["location"]["address"]
        det = details.get(pid) or {}
        if not det and (D / "details.jsonl").exists():
            for line in open(D / "details.jsonl"):
                if pid in line:
                    x = json.loads(line)
                    if x["property_id"] == pid:
                        det = V.parse_detail({**x, "details": {}})
        s = dict(pid=pid, x=a["coordinate"]["lon"] * V.KX, y=a["coordinate"]["lat"] * V.KY, type_group=V.TYPE_GROUP.get(d["type"]),
                 sqft=d["sqft"], beds=d["beds"], baths=d["baths"], lot=d["lot_sqft"], year=d["year_built"], zip=a["postal_code"],
                 city=a["city"], county=L["location"]["county"]["name"].lower(), address=a["line"], hoa=(L.get("hoa") or {}).get("fee"),
                 flip=0, below_share=V.below_share(d["sqft"], det), cond=V.condition_of(det.get("text") or ""),
                 **{k: det.get(k) for k in ("baths_full", "baths_half", "garage", "bsmt", "bsmt_fin", "walkout", "roof_new", "subdivision")})
        today = TODAY.isoformat()
        g = V.grid(s, pool, today, models)
        out[pid] = {"note": note, "ask": L["list_price"], "baseline_comp": V.baseline(s, pool, today), "grid": g and g["value"],
                    "condition": s["cond"], "tier": tier(det.get("text"))[0], "below_share": s["below_share"],
                    "comps": [{k: c[k] for k in ("address", "sold_price", "sqft", "year", "flags", "adjusted", "weight")} for c in (g or {}).get("comps", [])]}
    return out


if __name__ == "__main__":
    main()
