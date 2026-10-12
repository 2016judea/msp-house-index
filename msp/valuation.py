"""Comparable-sales valuation, two ways, both computed (no model writes a number).

baseline()  the method the index shipped with first: same kind of home, sqft ±25%,
            built within 25 years, the 10 nearest sold within 2 mi, median $/sqft × sqft.

grid()      an appraiser's adjusted comp grid. The 6 most similar sales (distance,
            recency, size, age, beds/baths, lot, condition, same subdivision) are each
            brought to today with a local monthly price index, then adjusted for every
            difference from the subject at rates measured from the area's own sales
            (fit_areas: one log-price regression per area, coefficients saved). The
            value is the weighted middle of the adjusted prices, weight falling with how
            much each comp had to be adjusted.

Both take `asof`: only sales before it are comps, so scripts/backtest.py can value a
home that has already sold exactly as it would have been valued the day before.
"""
import json, math, re, datetime as dt
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
TYPE_GROUP = {"single_family": "house", "townhomes": "townhome", "condos": "condo", "condo_townhome_rowhome_coop": "condo",
              "multi_family": "multi", "mobile": "mobile", "land": "land", "farm": "land"}
KX, KY = 111320 * math.cos(math.radians(45)), 110574  # metres per degree at 45°N

# Condition from the listing's own words. "Updated" alone is in half of all listings,
# so only a whole-house claim counts as renovated.
RENOVATED = re.compile(r"\b(completely|fully|totally|extensively|newly|beautifully|tastefully|thoughtfully|meticulously) (?:renovated|remodeled|remodelled|updated|rehabbed|restored)\b|"
                       r"\b(?:full|complete|total|gut|top[- ]to[- ]bottom|down to the studs) (?:renovation|remodel|rehab)\b|\bflip\b|\brenovated in 20\d\d\b|\beverything (?:is )?new\b", re.I)
NEEDS_WORK = re.compile(r"(\bas-is\b|\b(?:sold|selling|offered|listed|sale|purchase)\s+(?:in\s+)?[\"'“]?as is\b|\bas is[\"'”]? condition|\btlc\b|\bfixer\b|\bhandyman\b|\binvestor special\b|"
                        r"\bneeds (?:a lot of |some )?(?:work|updating|updates|repairs)\b|\bcash (?:offers )?only\b|\bcash or rehab\b|\brehab loan\b|\bmold\b|\bprice reflects\b|will not make any repairs|"
                        r"\bbring your (?:ideas|vision|tools|contractor)\b|\bsweat equity\b|\bfire[- ]damage|\bwater damage|\bfoundation (?:issues|problems)|\bmajor repairs|\bcomplete rehab|\bfull rehab|\bgutted\b(?![\s\S]{0,40}(?:remodel|rebuil|redone|updated)))", re.I)
NEW_ROOF = re.compile(r"\bnew(?:er)? roof\b|\broof (?:\(|replaced|new) ?(?:in )?20(?:1[89]|2\d)", re.I)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_detail(det):
    """Features an appraiser adjusts for, from one comp_details.jsonl record."""
    d = det.get("description") or {}
    lines = {}
    for cat, texts in (det.get("details") or {}).items():
        for t in texts:
            k, _, v = t.partition(":")
            lines[k.strip()] = v.strip() if _ else True
    text = d.get("text") or ""
    bsmt = (lines.get("Basement Description") or "")
    bl = bsmt.lower()
    has_bsmt = None if not bsmt else (0 if ("none" in bl or bl.strip() in ("crawl space", "slab")) else 1)
    fin = None if not bsmt else (1.0 if "finished (livable)" in bl and "partially" not in bl else 0.5 if "partially finished" in bl else 0.0)
    full = d.get("baths_full") or 0
    three = d.get("baths_3qtr") or 0
    half = d.get("baths_half") or 0
    roof = (lines.get("Roof") or "").lower()
    return dict(
        baths_full=(full + three) if (full or three or half) else None, baths_half=half if (full or three or half) else None,
        garage=min(d["garage"], 4) if d.get("garage") is not None else (_f(lines.get("Garage Spaces"))),
        bsmt=has_bsmt, bsmt_fin=fin if has_bsmt else (0.0 if has_bsmt == 0 else None), walkout=1 if "walkout" in bl else (0 if bsmt else None),
        stories=_f(d.get("stories")), lot=d.get("lot_sqft"),
        roof_new=1 if "8 years or less" in roof or NEW_ROOF.search(text) else (0 if roof else None),
        renovated=1 if (RENOVATED.search(text) or lines.get("Renovated") or lines.get("Rehabbed")) else 0,
        needs_work=1 if NEEDS_WORK.search(text) else 0,
        subdivision=(lines.get("Subdivision") or lines.get("Source Neighborhood") or None),
        apn=lines.get("Parcel Number"), has_text=bool(text),
        foundation=_f(lines.get("Foundation Area")),
        history=[(e.get("date"), e.get("event_name"), e.get("price")) for e in det.get("property_history") or []],
        text=text,
    )


def flip_flag(history, sold_date):
    """Bought 3-24 months before this sale: a quick resale, usually after a renovation.
    Never reads this sale's price, so a home being valued can carry it too."""
    if not sold_date:
        return 0, None
    t = dt.date.fromisoformat(sold_date[:10])
    for date, ev, p in history:
        if not date or (ev or "").lower() != "sold":
            continue
        d0 = dt.date.fromisoformat(date[:10])
        if 90 <= (t - d0).days <= 730:
            return 1, (date, p)
    return 0, None


def last_ask(history, sold_date):
    """The asking price in force when the home went under contract: the last list or
    price-change event of THIS listing run (after the previous sale, within a year), never
    a rent and never an older run's price. None when the run has no events."""
    t = sold_date[:10]
    t0 = (dt.date.fromisoformat(t) - dt.timedelta(days=365)).isoformat()
    prev = max((e[0][:10] for e in history if e[0] and e[0][:10] < t and (e[1] or "").lower() == "sold"), default="")
    ev = sorted((e for e in history if e[0] and max(prev, t0) < e[0][:10] < t and e[2] and e[2] >= 20000
                 and (e[1] or "").lower() in ("listed", "relisted", "price changed")), key=lambda e: e[0])
    return ev[-1][2] if ev else None


def below_share(sqft, det):
    """Share of the MLS finished sqft that is finished basement. MLS 'Total Square Feet
    Living' counts a finished basement; a full finished basement is about the foundation's
    footprint. 0 when the basement is unfinished or absent, None when unknown."""
    if not det or det.get("bsmt") is None:
        return None
    if not det["bsmt"] or not det.get("bsmt_fin"):
        return 0.0
    fa = det.get("foundation")
    if not fa or fa >= sqft:
        return None
    return round(min(fa * det["bsmt_fin"] / sqft, 0.6), 3)


_COND = None


def condition_groups():
    """Measured condition phrases (scripts/mine_condition.py → data/condition_phrases.json):
    a group counts when 20+ sales carry it and its effect is clear of noise (|t| ≥ 2.5)."""
    global _COND
    if _COND is None:
        f = D / "condition_phrases.json"
        g = json.loads(f.read_text())["groups"] if f.exists() else {}
        _COND = [(name, re.compile(v["pattern"], re.I), v["effect"], v["n"]) for name, v in g.items()
                 if v["n"] >= 20 and abs(v["t"]) >= 2.5 and abs(v["effect"]) >= 0.03]
        CONDN.update({name: n for name, _, _, n in _COND})
    return _COND


CONDN = {}


def condition_of(text):
    """(effect, groups): the strongest needs-work group the listing's words match, else the
    renovated group's premium, else 0. Effect is the measured median sale vs comps."""
    if not text:
        return 0.0, []
    hit = [(name, eff, n) for name, rx, eff, n in condition_groups() if rx.search(text)]
    neg = [h for h in hit if h[1] < 0]
    if neg:
        worst = min(neg, key=lambda h: h[1])
        return worst[1], [h[0] for h in sorted(neg, key=lambda h: h[1])]
    pos = [h for h in hit if h[1] > 0]
    return (pos[0][1], [pos[0][0]]) if pos else (0.0, [])


def load_details():
    out = {}
    p = D / "comp_details.jsonl"
    if p.exists():
        for line in open(p):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[r["property_id"]] = parse_detail(r)
    return out


class Pool:
    """Every closed sale, as arrays. One row per (property, sale date)."""

    def __init__(self, details=None):
        details = load_details() if details is None else details
        seen, rows = set(), []
        for line in open(D / "sold_raw.jsonl"):
            r = json.loads(line)
            d = r.get("description") or {}
            a = (r.get("location") or {}).get("address") or {}
            co = a.get("coordinate") or {}
            price, date = d.get("sold_price") or r.get("last_sold_price"), d.get("sold_date") or r.get("last_sold_date")
            key = (r["property_id"], date)
            if key in seen or not price or not date or not d.get("sqft") or not co.get("lat"):
                continue
            # A $/sqft outside any Twin Cities home is a data error (a whole building's
            # price on one unit, a sqft typo): the same filter build_index.py applies.
            if d["sqft"] < 300 or not 30 <= price / d["sqft"] <= 1000:
                continue
            seen.add(key)
            det = details.get(r["property_id"]) or {}
            # A new build's earlier "sale" is its lot, not a flip.
            new = d.get("year_built") and d["year_built"] >= int(date[:4]) - 2
            flip, flip_from = (0, None) if new else flip_flag(det.get("history") or [], date)
            rows.append(dict(
                pid=r["property_id"], type_group=TYPE_GROUP.get(d.get("type"), d.get("type")), price=price, date=date,
                sqft=d["sqft"], year=d.get("year_built"), beds=d.get("beds"), baths=d.get("baths"), lot=d.get("lot_sqft"),
                lat=co["lat"], lon=co["lon"], address=a.get("line"), city=a.get("city"), zip=a.get("postal_code"),
                county=(((r.get("location") or {}).get("county") or {}).get("name") or r["_county_searched"]).lower(),
                photo=((r.get("primary_photo") or {}).get("href") or "").replace("http:", "https:").replace("s.jpg", "rd-w480_h360.jpg") or None,
                url=r.get("href"), hoa=(r.get("hoa") or {}).get("fee"),
                has_detail=bool(det), flip=flip, flip_from=flip_from,
                list_price=last_ask(det.get("history") or [], date) or r.get("list_price"),
                run=run_of(det.get("history") or [], date),
                text=det.get("text") or "",
                cond=condition_of(det.get("text") or ""),
                below_share=below_share(d["sqft"], det),
                **{k: det.get(k) for k in ("baths_full", "baths_half", "garage", "bsmt", "bsmt_fin", "walkout", "stories", "roof_new", "renovated", "needs_work", "subdivision", "apn")},
            ))
        self.rows = rows
        self.n = len(rows)
        A = lambda k, dt_=float: np.array([np.nan if r[k] is None else r[k] for r in rows], dtype=dt_)
        self.price = A("price")
        self.sqft = A("sqft")
        self.year = A("year")
        self.beds = A("beds")
        self.baths = A("baths")
        self.lot = A("lot")
        self.x = A("lon") * KX
        self.y = A("lat") * KY
        self.dord = np.array([dt.date.fromisoformat(r["date"][:10]).toordinal() for r in rows])
        self.month = np.array([r["date"][:7] for r in rows])
        self.type_group = np.array([r["type_group"] or "" for r in rows])
        self.pid = np.array([r["pid"] for r in rows])
        self.tree = cKDTree(np.c_[self.x, self.y])


PIN_LEN = {"hennepin": 13, "washington": 13, "ramsey": 12, "dakota": 12, "anoka": 12, "scott": 9, "carver": 9}


def norm_pin(county, apn):
    digits = "".join(ch for ch in (apn or "") if ch.isdigit())
    return digits.zfill(PIN_LEN.get(county, len(digits))) if digits else None


def attach_emv(pool):
    """County EMV for every sale whose listing printed its parcel number."""
    import duckdb
    con = duckdb.connect()
    con.execute(f"attach '{D / 'county.duckdb'}' as cty (read_only)")
    keys = [(i, r["county"], norm_pin(r["county"], r.get("apn"))) for i, r in enumerate(pool.rows)]
    con.execute("create table K (i int, county varchar, pin varchar)")
    pool.emv = np.full(pool.n, np.nan)
    if not any(k[2] for k in keys):
        return pool
    con.executemany("insert into K values (?, ?, ?)", [k for k in keys if k[2]])
    for i, emv in con.execute("select k.i, p.emv_total from K k join cty.parcels p using (county, pin) where p.emv_total > 0").fetchall():
        pool.emv[i] = emv
        pool.rows[i]["emv"] = emv
    return pool


# ---------------------------------------------------------------- baseline ---

def bkey(addr):
    """House number + first street word: the same building."""
    m = re.match(r"^(\d+\s+\S+)", addr or "")
    return m.group(1) if m else None


def baseline(s, pool, asof):
    """The shipped method. s: dict with x, y, type_group, sqft, year, address, pid."""
    if not s.get("sqft"):
        return None
    t = dt.date.fromisoformat(str(asof)[:10]).toordinal()
    idx = np.array(pool.tree.query_ball_point([s["x"], s["y"]], 3200), dtype=int)
    if not len(idx):
        return None
    m = (pool.type_group[idx] == s["type_group"]) & (pool.dord[idx] < t) & (pool.pid[idx] != s["pid"])
    m &= (pool.sqft[idx] >= s["sqft"] * 0.75) & (pool.sqft[idx] <= s["sqft"] * 1.25)
    if s.get("year"):
        yy = pool.year[idx]
        m &= np.isnan(yy) | (np.abs(yy - s["year"]) <= 25)
    idx = idx[m]
    # A condo's own building first: 2+ units sold in it replace the neighbourhood.
    if s["type_group"] == "condo" and bkey(s.get("address")):
        k = bkey(s["address"])
        alli = np.array(pool.tree.query_ball_point([s["x"], s["y"]], 212), dtype=int)
        same = [i for i in alli if pool.type_group[i] == "condo" and pool.dord[i] < t and pool.pid[i] != s["pid"]
                and abs(pool.x[i] - s["x"]) < 150 and abs(pool.y[i] - s["y"]) < 150 and bkey(pool.rows[i]["address"]) == k]
        if len(same) >= 2:
            return float(np.median(pool.price[same] / pool.sqft[same]) * s["sqft"])
    if len(idx) < 4:
        return None
    dist = np.hypot(pool.x[idx] - s["x"], pool.y[idx] - s["y"])
    near = idx[np.argsort(dist)[:10]]
    return float(np.median(pool.price[near] / pool.sqft[near]) * s["sqft"])


# ------------------------------------------------------------ area models ---
# One log-price regression per area × kind, on the area's own sales before a cutoff.
# Each coefficient is a measured adjustment rate: e.g. +0.031 on baths_full means a
# full bath more sells for ~3.1% more, everything else in the regression equal.

FEATURES = [  # (key, label, unit for the reader)
    ("lsqft", "size", "log sqft"), ("beds", "bedroom", "bedroom"), ("baths_full", "full bath", "full bath"),
    ("baths_half", "half bath", "half bath"), ("garage", "garage stall", "stall"), ("bsmt", "basement", "basement"),
("walkout", "walkout", "walkout"), ("llot", "lot size", "log lot"),
    ("roof_new", "roof 8 years or newer", "new roof"), ("flip", "bought and resold renovated", "flip"), ("hoa", "HOA dues", "$100/mo"),
    ("below_share", "share of the sqft below grade", "all of it"),
]
AGE_BINS = [(0, 1920, "before 1920"), (1920, 1940, "1920s-30s"), (1940, 1960, "1940s-50s"), (1960, 1980, "1960s-70s"),
            (1980, 2000, "1980s-90s"), (2000, 2015, "2000-2014"), (2015, 3000, "2015 or later")]
SIGN = {"lsqft": 1, "beds": 1, "baths_full": 1, "baths_half": 1, "garage": 1, "bsmt": 1, "bsmt_fin": 1, "walkout": 1, "llot": 1,
        "roof_new": 1, "renovated": 1, "needs_work": -1, "hoa": -1, "below_share": -1}
DETAIL_KEYS = ("below_share", "baths_full", "baths_half", "garage", "bsmt", "bsmt_fin", "walkout", "roof_new")
MIN_AREA_N = 600
BLEND = 0.5  # share of the adjusted grid vs the EMV-ratio comps in the comp value; set from the backtest


def area_of(r):
    city = (r.get("city") or "").lower()
    if city in ("minneapolis", "saint paul", "st paul", "st. paul"):
        return "Saint Paul" if "paul" in city else "Minneapolis"
    return (r.get("county") or "").title() + " County"


def kind_of(tg):
    return "house" if tg in ("house", "multi") else "attached"


def feat(r):
    """Feature vector source for one home (a sold row, or a listing in the same shape)."""
    lot = r.get("lot")
    yb = r.get("year")
    f = {
        "lsqft": math.log(r["sqft"]),
        "beds": min(max(r.get("beds") or 0, 0), 6) if r.get("beds") is not None else None,
        "baths_full": r.get("baths_full") if r.get("baths_full") is not None else None,
        "baths_half": r.get("baths_half"),
        "garage": None if r.get("garage") is None else min(r["garage"], 4),
        "bsmt": r.get("bsmt"), "bsmt_fin": r.get("bsmt_fin"), "walkout": r.get("walkout"),
        "llot": math.log(min(max(lot, 1500), 220000)) if lot else None,
        "roof_new": r.get("roof_new"), "renovated": r.get("renovated") or 0, "needs_work": r.get("needs_work") or 0,
        "flip": r.get("flip") or 0, "hoa": min((r.get("hoa") or 0), 2000) / 100,
        "below_share": r.get("below_share"),
        "age": next((b[2] for b in AGE_BINS if yb and b[0] <= yb < b[1]), None),
    }
    if f["baths_full"] is None and r.get("baths") is not None:  # no detail: the total, read as full baths
        f["baths_full_imputed"] = r["baths"]
    return f


class AreaModel:
    def __init__(self, area, kind, rows, months):
        self.area, self.kind, self.n = area, kind, len(rows)
        feats = [k for k, _, _ in FEATURES if not (kind == "house" and k == "hoa") and not (kind == "attached" and k in ("llot", "walkout"))]
        fs = [feat(r) for r in rows]
        # Impute a missing feature with the area mean, and let an indicator carry the gap.
        self.means = {}
        for k in feats:
            v = [f[k] if f[k] is not None else f.get(k + "_imputed") for f in fs]
            v = [x for x in v if x is not None]
            self.means[k] = float(np.mean(v)) if v else 0.0
        self.known = {k: sum(1 for f in fs if f[k] is not None) for k in feats}
        feats = [k for k in feats if self.known[k] >= 100 and np.std([f[k] for f in fs if f[k] is not None]) > 0]
        self.zips = sorted({r["zip"] for r in rows if r.get("zip")})
        self.ages = [b[2] for b in AGE_BINS]
        self.months = months
        y = np.log([r["price"] for r in rows])
        # A rate with the wrong sign (a bedroom worth less, once size is held equal) is
        # collinearity, not a market fact an appraiser would apply: drop it and refit.
        self.dropped = {}
        for _ in range(4):
            self.feats = feats
            cols = feats + ["nodetail"] + [f"age:{a}" for a in self.ages[1:]] + [f"m:{m}" for m in months[1:]] + [f"z:{z}" for z in self.zips[1:]] + ["tg:condo"] * (kind == "attached")
            self.cols = cols
            X = np.zeros((len(rows), len(cols)))
            ci = {c: i for i, c in enumerate(cols)}
            for j, (r, f) in enumerate(zip(rows, fs)):
                X[j, :] = self._row(r, f, ci, r["date"][:7])
            Xc = np.c_[np.ones(len(rows)), X]
            # Light ridge on the dummies (sparse zips and months), ~none on the features.
            lam = np.array([0] + [0.01 if c in feats or c == "nodetail" else 3.0 for c in cols])
            beta = np.linalg.solve(Xc.T @ Xc + np.diag(lam), Xc.T @ y)
            self.b0, self.beta = beta[0], dict(zip(cols, beta[1:]))
            wrong = [k for k in feats if SIGN.get(k) and self.beta[k] * SIGN[k] < 0]
            if not wrong:
                break
            for k in wrong:
                self.dropped[k] = round(self.beta[k], 4)
            feats = [k for k in feats if k not in wrong]
        resid = y - Xc @ beta
        self.rmse = float(np.sqrt(np.mean(resid ** 2)))
        self.median_price = float(np.median([r["price"] for r in rows]))
        last = months[-1]
        self.index = {m: math.exp(self.beta.get(f"m:{m}", 0.0)) for m in months}  # relative to the first month
        self.index_n = {m: sum(1 for r in rows if r["date"][:7] == m) for m in months}

    def _row(self, r, f, ci, month):
        v = np.zeros(len(self.cols))
        nodetail = 0
        for k in self.feats:
            x = f[k]
            if x is None:
                x = f.get(k + "_imputed") if k == "baths_full" and f.get("baths_full_imputed") is not None else self.means[k]
                if k in DETAIL_KEYS:
                    nodetail = 1
            v[ci[k]] = x
        v[ci["nodetail"]] = nodetail
        a = f["age"]
        if a and f"age:{a}" in ci:
            v[ci[f"age:{a}"]] = 1
        if f"m:{month}" in ci:
            v[ci[f"m:{month}"]] = 1
        if r.get("zip") and f"z:{r['zip']}" in ci:
            v[ci[f"z:{r['zip']}"]] = 1
        if "tg:condo" in ci and r.get("type_group") == "condo":
            v[ci["tg:condo"]] = 1
        return v

    def month_effect(self, month):
        """Log price level of a month; past the last fitted month, the last one."""
        # A month with under a third of the usual sales (the current, partial one) is noise.
        floor = 0.3 * float(np.median(list(self.index_n.values())))
        ms = [m for m in self.months if m <= month and self.index_n.get(m, 0) >= floor]
        m = ms[-1] if ms else self.months[0]
        return self.beta.get(f"m:{m}", 0.0), m

    def age_effect(self, a):
        return self.beta.get(f"age:{a}", 0.0) if a else None

    def zip_effect(self, z):
        if z in self.zips:
            return self.beta.get(f"z:{z}", 0.0)
        return None

    def predict(self, r, month):
        ci = {c: i for i, c in enumerate(self.cols)}
        f = feat(r)
        x = self._row(r, f, ci, month)
        lm, _ = self.month_effect(month)
        x[[ci[c] for c in self.cols if c.startswith("m:")]] = 0
        return math.exp(self.b0 + sum(self.beta[c] * x[ci[c]] for c in self.cols) + lm)

    def summary(self):
        rate = lambda k: round(self.beta[k], 4)
        mp = self.median_price
        out = {"area": self.area, "kind": self.kind, "n": self.n, "rmse_log": round(self.rmse, 3), "median_price": round(mp),
               "rates": {}, "dropped_wrong_sign": self.dropped, "age": {a: round(self.beta.get(f"age:{a}", 0.0), 4) for a in self.ages},
               "index": {m: round(v, 4) for m, v in self.index.items()}, "index_n": self.index_n}
        for k in self.feats:
            b = self.beta[k]
            dollars = mp * b * (0.1 if k == "lsqft" else 0.25 if k == "llot" else 1)
            out["rates"][k] = {"coef": rate(k), "known_n": self.known[k],
                               "dollars_at_median": round(dollars, -2),
                               "per": "10% more sqft" if k == "lsqft" else "25% more lot" if k == "llot" else dict((a, c) for a, _, c in FEATURES)[k]}
        return out


class Models:
    """Fitted area models. cutoff: only sales before it train (the backtest refits per month)."""

    def __init__(self, pool, holdout_from=None):
        self.pool = pool
        self.holdout_from = holdout_from
        self.fits = {}

    def for_date(self, asof):
        """The fit a valuation dated `asof` may use: trained on sales before the 1st of its
        month (or before the holdout start, whichever is later)."""
        if self.holdout_from is None:
            cut = "9999"
        else:
            cut = max(self.holdout_from, str(asof)[:7] + "-01")
        if cut not in self.fits:
            self.fits[cut] = self._fit(cut)
        return self.fits[cut]

    def _fit(self, cut):
        groups = {}
        for r in self.pool.rows:
            if r["date"] < cut and r["type_group"] in ("house", "townhome", "condo"):
                groups.setdefault((area_of(r), kind_of(r["type_group"])), []).append(r)
        # Small areas borrow from their county, then from the metro.
        months = sorted({r["date"][:7] for g in groups.values() for r in g})
        fits = {}
        for (area, kind), rows in groups.items():
            if len(rows) >= MIN_AREA_N:
                fits[(area, kind)] = AreaModel(area, kind, rows, months)
        for kind in ("house", "attached"):
            allrows = [r for (a, k), g in groups.items() if k == kind for r in g]
            fits[("Metro", kind)] = AreaModel("Twin Cities metro", kind, allrows, months)
        return fits

    @staticmethod
    def settings():
        return {k: globals()[k] for k in ("N_COMPS", "TIME_ADJ", "S_DIST", "S_MONTHS", "S_SIZE", "S_YEAR", "S_ROOM", "S_COND",
                                          "USE_LOCATION", "GROSS_FLOOR", "BLEND")}

    def summaries(self):
        """Every fitted area model, as saved coefficients (data/valuation_model.json)."""
        fits = self.for_date("9999")
        return {"built": dt.date.today().isoformat(), "settings": {k: globals()[k] for k in
                ("N_COMPS", "TIME_ADJ", "S_DIST", "S_MONTHS", "S_SIZE", "S_YEAR", "S_ROOM", "S_COND", "USE_LOCATION", "GROSS_FLOOR", "BLEND")},
                "areas": [m.summary() for m in fits.values()]}

    def model(self, r, asof):
        fits = self.for_date(asof)
        k = kind_of(r["type_group"])
        return fits.get((area_of(r), k)) or fits[("Metro", k)]


# ------------------------------------------------------------------- grid ---

N_COMPS = 6
# Similarity scales: one unit of difference in each costs the same. Tuned on --validate.
S_DIST, S_MONTHS, S_SIZE, S_YEAR, S_ROOM, S_COND = 800.0, 6.0, 0.15, 15.0, 0.5, 2.0
TIME_ADJ = 1.0       # share of the measured index change applied (1 = all of it)
USE_LOCATION = 1      # adjust for ZIP when a comp sits in another one
GROSS_FLOOR = 0.05    # reconciliation weight ∝ 1 / (similarity + 0.5) / (GROSS_FLOOR + gross adjustment)
LABEL = {"lsqft": "size", "beds": "bedrooms", "baths_full": "full baths", "baths_half": "half baths", "garage": "garage stalls",
         "bsmt": "basement", "bsmt_fin": "finished basement", "walkout": "walkout basement", "llot": "lot", "roof_new": "roof 8 years or newer",
         "renovated": "renovated", "needs_work": "needs work", "flip": "recently renovated for resale", "hoa": "HOA dues",
         "below_share": "basement share of the sqft"}


def _similarity(s, sf, pool, idx, t):
    """Lower is more similar. Each term is in 'one unit of acceptable difference'."""
    dist = np.hypot(pool.x[idx] - s["x"], pool.y[idx] - s["y"])
    months = (t - pool.dord[idx]) / 30.4
    size = np.log(pool.sqft[idx] / s["sqft"]) / 0.15
    size = size * (0.15 / S_SIZE)
    sc = (dist / S_DIST) ** 2 + (months / S_MONTHS) ** 2 + size ** 2
    if s.get("year"):
        yy = np.where(np.isnan(pool.year[idx]), s["year"], pool.year[idx])
        sc += ((yy - s["year"]) / S_YEAR) ** 2
    if s.get("beds") is not None:
        bb = np.where(np.isnan(pool.beds[idx]), s["beds"], pool.beds[idx])
        sc += S_ROOM * (bb - s["beds"]) ** 2
    if s.get("baths") is not None:
        bt = np.where(np.isnan(pool.baths[idx]), s["baths"], pool.baths[idx])
        sc += S_ROOM * (bt - s["baths"]) ** 2
    if s.get("lot") and kind_of(s["type_group"]) == "house":
        ll = np.where(np.isnan(pool.lot[idx]), s["lot"], pool.lot[idx])
        sc += (np.log(np.clip(ll, 1500, 220000) / min(max(s["lot"], 1500), 220000)) / 0.5) ** 2
    rows = [pool.rows[i] for i in idx]
    # Condition: a dated house is compared with dated houses first. Difference in the
    # measured condition effect, in units of 10%.
    se = (s.get("cond") or (0.0, []))[0]
    cond = np.array([abs((r.get("cond") or (0.0, []))[0] - se) / 0.10 for r in rows])
    sc += S_COND * cond ** 2
    sc += np.array([0 if r["type_group"] == s["type_group"] else 3.0 for r in rows])
    sub = s.get("subdivision")
    same_sub = np.array([bool(sub) and r.get("subdivision") == sub for r in rows])
    same_bldg = np.array([s["type_group"] == "condo" and r["type_group"] == "condo" and bkey(r["address"]) == bkey(s.get("address")) and d < 150
                          for r, d in zip(rows, dist)])
    sc = np.where(same_bldg, sc * 0.3, np.where(same_sub, sc * 0.7, sc))
    return sc, dist, months, same_sub, same_bldg


def grid(s, pool, asof, models, n_comps=None):
    """The adjusted comp grid for one subject. Returns None when no comps qualify."""
    if not s.get("sqft") or s.get("type_group") not in ("house", "townhome", "condo", "multi"):
        return None
    n_comps = n_comps or N_COMPS
    t = dt.date.fromisoformat(str(asof)[:10]).toordinal()
    month = str(asof)[:7]
    kind = kind_of(s["type_group"])
    for radius in (3000, 8000, 20000):
        idx = np.array(pool.tree.query_ball_point([s["x"], s["y"]], radius), dtype=int)
        if not len(idx):
            continue
        tg = pool.type_group[idx]
        ok = (pool.dord[idx] < t) & (pool.pid[idx] != s["pid"]) & np.isin(tg, ["house", "multi"] if kind == "house" else ["townhome", "condo"])
        ok &= (pool.sqft[idx] >= s["sqft"] * 0.6) & (pool.sqft[idx] <= s["sqft"] * 1.6)
        idx = idx[ok]
        if len(idx) >= n_comps:
            break
    if len(idx) < 3:
        return None
    sf = feat(s)
    sc, dist, months, same_sub, same_bldg = _similarity(s, sf, pool, idx, t)
    order = np.argsort(sc)[:n_comps]
    m = models.model(s, asof)
    m_now, m_month = m.month_effect(month)
    comps = []
    for o in order:
        i = idx[o]
        r = pool.rows[i]
        cf = feat(r)
        m_then, _ = m.month_effect(r["date"][:7])
        time_adj = r["price"] * (math.exp(TIME_ADJ * (m_now - m_then)) - 1)
        base = r["price"] + time_adj
        lines = []
        if abs(time_adj) >= 500:
            lines.append({"k": "time", "amt": round(time_adj, -2), "why": f"sold {r['date'][:7]}; {m.area} prices {'rose' if time_adj > 0 else 'fell'} "
                          f"{abs(math.exp(TIME_ADJ * (m_now - m_then)) - 1):.1%} from then to {m_month} (monthly index from {m.n:,} sales)"})
        for k in m.feats:
            a, b = sf.get(k), cf.get(k)
            if k == "baths_full":
                a = a if a is not None else sf.get("baths_full_imputed")
                b = b if b is not None else cf.get("baths_full_imputed")
            if a is None or b is None or a == b:
                continue
            delta = a - b
            amt = base * m.beta[k] * delta
            if abs(amt) < 500:
                continue
            if k == "lsqft":
                why = f"{s['sqft']:,} vs {r['sqft']:,} sqft: {m.beta[k] * math.log(1.1):.1%} per 10% more space"
            elif k == "llot":
                why = f"lot {s['lot']:,} vs {r['lot']:,} sqft: {m.beta[k] * math.log(1.25):.1%} per 25% more lot"
            elif k == "below_share":
                why = (f"{a:.0%} vs {b:.0%} of the sqft is finished basement: a below-grade foot sells for "
                       f"{1 + m.beta[k]:.0%} of an above-grade one")
            elif k == "hoa":
                why = f"HOA ${(s.get('hoa') or 0):,.0f} vs ${(r.get('hoa') or 0):,.0f}/mo: {m.beta[k]:.1%} per $100/mo"
            else:
                why = f"{LABEL[k]} {a:g} vs {b:g}: {m.beta[k]:+.1%} each" if k in ("beds", "baths_full", "baths_half", "garage") else \
                      f"{LABEL[k]}: {'subject yes, comp no' if delta > 0 else 'comp yes, subject no'} ({m.beta[k]:+.1%})"
            lines.append({"k": k, "amt": round(amt, -2), "why": why + f", measured from {m.known[k]:,} {m.area} sales"})
        ae, be = m.age_effect(sf["age"]), m.age_effect(cf["age"])
        if ae is not None and be is not None and sf["age"] != cf["age"]:
            amt = base * (ae - be)
            if abs(amt) >= 500:
                lines.append({"k": "age", "amt": round(amt, -2), "why": f"built {s['year']} vs {r['year']}: homes built {sf['age']} sell {ae - be:+.1%} against {cf['age']} in {m.area}"})
        za, zb = m.zip_effect(s.get("zip")), m.zip_effect(r.get("zip"))
        if USE_LOCATION and za is not None and zb is not None and s.get("zip") != r.get("zip"):
            amt = base * (za - zb)
            if abs(amt) >= 500:
                lines.append({"k": "location", "amt": round(amt, -2), "why": f"ZIP {s['zip']} sells {za - zb:+.1%} against {r['zip']}, everything else equal"})
        ce, cg = s.get("cond") or (0.0, [])
        re_, rg = r.get("cond") or (0.0, [])
        if ce != re_:
            amt = base * (ce - re_)
            if abs(amt) >= 500:
                say = lambda e, g: f"listing reads '{g[0]}' ({e:+.1%} against comps)" if g else "no condition words (0%)"
                lines.append({"k": "condition", "amt": round(amt, -2), "why": f"subject {say(ce, cg)}, comp {say(re_, rg)}; "
                              f"measured from {CONDN.get(cg[0] if cg else rg[0], 0):,} sales whose listings used those words"})
        adj = sum(l["amt"] for l in lines if l["k"] != "time")
        gross = sum(abs(l["amt"]) for l in lines if l["k"] != "time") / base
        comps.append(dict(i=int(i), pid=r["pid"], address=r["address"], city=r["city"], photo=r["photo"], url=r["url"], sold_date=r["date"],
                          sold_price=r["price"], dist_m=round(float(dist[o])), sqft=r["sqft"], beds=r["beds"], baths=r["baths"], year=r["year"],
                          lot=r["lot"], garage=r.get("garage"), bsmt_fin=r.get("bsmt_fin"),
                          flags=list((r.get("cond") or (0, []))[1]) + [x for x, on in (("bought & resold within 2 yrs", r.get("flip")),
                                                 ("roof ≤8 yrs", r.get("roof_new") == 1), ("finished basement", r.get("bsmt_fin") == 1)) if on],
                          same_building=bool(same_bldg[o]), same_subdivision=bool(same_sub[o]), similarity=round(float(sc[o]), 2),
                          time_adjusted=round(base, -2), lines=lines, adjusted=round(base + adj, -2), gross=round(gross, 3)))
    w = np.array([1 / (0.5 + c["similarity"]) / (GROSS_FLOOR + c["gross"]) for c in comps])
    w = w / w.sum()
    for c, x in zip(comps, w):
        c["weight"] = round(float(x), 3)
    vals = np.array([c["adjusted"] for c in comps])
    value = float((w * vals).sum())
    # Second opinion from the same comps: what each sold for against the county's own
    # value of it (EMV), applied to the subject's EMV. The assessor has already priced
    # size, quality and lot, so this needs no adjustments, only the time index.
    emv_value = None
    if s.get("emv") and hasattr(pool, "emv"):
        # A sale at under half or over twice its EMV was matched to the wrong parcel (a condo's
        # garage stall, a common-area lot): left in, one put a $190k condo at $7M (2026-10-12).
        rs = [(c["time_adjusted"] / pool.emv[c["i"]], c["weight"]) for c in comps
              if not np.isnan(pool.emv[c["i"]]) and 0.5 <= c["sold_price"] / pool.emv[c["i"]] <= 2.0]
        if len(rs) >= 3:
            rs.sort()
            tot, acc = sum(x for _, x in rs), 0
            for ratio, x in rs:
                acc += x
                if acc >= tot / 2:
                    break
            emv_value = s["emv"] * ratio
            for c in comps:
                e = pool.emv[c["i"]]
                c["emv"] = None if np.isnan(e) else float(e)
    return {"value": float(round(value, -3)), "emv_value": float(round(emv_value, -3)) if emv_value else None, "emv_ratio": round(ratio, 3) if emv_value else None, "model": f"{m.area} {m.kind}", "model_n": m.n, "month": m_month, "comps": comps,
            "range": [round(float(vals.min()), -3), round(float(vals.max()), -3)]}


def reconcile(g):
    """The comp value: the adjusted grid, blended with the EMV-ratio reading of the same comps."""
    if not g:
        return None
    if g.get("emv_value"):
        return BLEND * g["value"] + (1 - BLEND) * g["emv_value"]
    return g["value"]


# ------------------------------------------------------------- production ---

def value_listings(listings, asof=None):
    """Grid for every listing, as of today, on every sale to date.
    listings: build_index.py rows (property_id, lat, lon, type_group, sqft, beds, baths,
    lot_sqft, year_built, zip, city, county, address, emv, hoa). Returns {property_id: grid}."""
    asof = asof or dt.date.today().isoformat()
    details = load_details()
    # A listing fetched only by fetch_details.py still has its own words and garage.
    if (D / "details.jsonl").exists():
        for line in open(D / "details.jsonl"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r["property_id"] not in details:
                details[r["property_id"]] = parse_detail({**r, "details": {}})
    pool = attach_emv(Pool(details))
    models = Models(pool)
    out = {}
    for L in listings:
        if not (L.get("lat") and L.get("sqft")):
            continue
        det = details.get(L["property_id"]) or {}
        s = dict(pid=L["property_id"], x=L["lon"] * KX, y=L["lat"] * KY, type_group=L.get("type_group"), sqft=L["sqft"],
                 beds=L.get("beds"), baths=L.get("baths"), lot=L.get("lot_sqft"), year=L.get("year_built"), zip=L.get("zip"),
                 city=L.get("city"), county=L.get("county"), address=L.get("address"), emv=L.get("emv"), hoa=L.get("hoa"),
                 below_share=below_share(L["sqft"], det), cond=condition_of(det.get("text") or ""),
                 flip=0 if (L.get("year_built") or 0) >= int(asof[:4]) - 2 else flip_flag(det.get("history") or [], asof)[0],
                 **{k: det.get(k) for k in ("baths_full", "baths_half", "garage", "bsmt", "bsmt_fin", "walkout", "roof_new",
                                                      "renovated", "needs_work", "subdivision")})
        g = grid(s, pool, asof, models)
        if g:
            g["comp_value"] = float(round(reconcile(g), -3))
            g["subject"] = {k: s.get(k) for k in ("sqft", "beds", "baths", "baths_full", "baths_half", "lot", "year", "garage", "bsmt_fin",
                                                   "walkout", "roof_new", "below_share", "cond")}
            out[L["property_id"]] = g
    return out, models


# ------------------------------------------------------------- ask model ---
# The asking price is information. A listing agent who prices a house 40% under every
# estimate, with no reason we can see, usually knows something we don't (2026-10-11,
# 8031 Tierneys Woods Curv). Measured on closed sales: where the sale landed, given our
# estimate and the last asking price. Expected sale = estimate × exp(f(gap)), with
# gap = log(ask ÷ estimate), f piecewise linear, fitted by least squares with the worst
# 2% of residuals trimmed.

ASK_KNOTS = (-0.3, -0.15, 0.0, 0.15)


class AskModel:
    def __init__(self, beta, n, knots=ASK_KNOTS, table=None):
        self.beta, self.n, self.knots, self.table = list(beta), n, knots, table

    @staticmethod
    def _x(g, cneg):
        g = min(max(g, -0.8), 0.5)
        return [1.0, g] + [max(0.0, g - k) for k in ASK_KNOTS] + [cneg, cneg * min(g, 0.0)]

    @classmethod
    def fit(cls, rows):
        """rows: (estimate, ask, sale, (cond_effect, groups))."""
        X, y = [], []
        for est, ask, sale, cond in rows:
            cneg = 1.0 if cond and cond[0] < 0 else 0.0
            X.append(cls._x(math.log(ask / est), cneg))
            y.append(math.log(sale / est))
        X, y = np.array(X), np.array(y)
        keep = np.ones(len(y), bool)
        for _ in range(2):
            b = np.linalg.lstsq(X[keep], y[keep], rcond=None)[0]
            r = np.abs(y - X @ b)
            keep = r <= np.quantile(r, 0.98)
        m = cls(b, int(keep.sum()))
        gaps = np.array([x[1] for x in X])
        tab = []
        for lo, hi, g in ((-9, -0.45, -0.5), (-0.45, -0.35, -0.4), (-0.35, -0.25, -0.3), (-0.25, -0.15, -0.2), (-0.15, -0.05, -0.1),
                          (-0.05, 0.05, 0.0), (0.05, 0.15, 0.1), (0.15, 9, 0.2)):
            sel = (gaps > lo) & (gaps <= hi)
            f = float(np.dot(cls._x(g, 0.0), b))
            tab.append({"ask_vs_estimate": round(math.exp(g) - 1, 3), "n": int(sel.sum()),
                        "sale_vs_estimate_fit": round(math.exp(f) - 1, 3),
                        "sale_vs_estimate_observed_median": round(float(np.exp(np.median(y[sel])) - 1), 3) if sel.sum() else None,
                        "sale_vs_ask_fit": round(math.exp(f - g) - 1, 3)})
        m.table = tab
        return m

    def predict(self, est, ask, cond=None):
        cneg = 1.0 if cond and cond[0] < 0 else 0.0
        g = min(max(math.log(ask / est), -0.8), 0.5)
        out = est * math.exp(float(np.dot(self._x(g, cneg), self.beta)))
        # Never further from the ask than closed sales in any gap band were (the curve's own
        # sale-vs-ask range): past the data a piecewise line would invent a discount.
        rng = [c["sale_vs_ask_fit"] for c in (self.table or []) if c["n"] >= 30]
        if rng:
            out = min(max(out, ask * (1 + min(rng))), ask * (1 + max(rng)))
        return float(out)

    def summary(self):
        return {"beta": [round(b, 5) for b in self.beta], "knots": list(self.knots), "n": self.n,
                "terms": ["intercept", "gap"] + [f"gap past {k:+.2f}" for k in self.knots] + ["needs-work words", "needs-work words × gap below 0"],
                "curve": self.table}

    @classmethod
    def load(cls, path=None):
        f = Path(path or D / "ask_model.json")
        if not f.exists():
            return None
        j = json.loads(f.read_text())
        return cls(j["beta"], j["n"], tuple(j["knots"]), j.get("curve"))


# ------------------------------------------------------- sale-to-list model ---
# "Room to negotiate" (Aidan, 2026-10-12): what a listing will really sell for, from
# what similar listings did. log(sale ÷ last ask) regressed on inputs a listing has while
# it is for sale: days listed, price cuts, relisting, ask against our records value,
# area, price band, type, season, condition words, HOA. Plain least squares; every
# coefficient is saved and printed.

CLOSE_LAG = 35  # days from accepted offer to recorded close (MN median ~30-40): a sold home's
                # days-on-market is its listing-to-close span minus this, to match an active listing's clock
DOM_BINS = [(0, 15, "under 2 weeks"), (15, 46, "2-6 weeks"), (46, 91, "6-13 weeks"), (91, 99999, "3+ months")]  # 6+ months had too few to stand alone
BANDS = [(0, 200e3, "under $200k"), (200e3, 350e3, "$200-350k"), (350e3, 500e3, "$350-500k"), (500e3, 750e3, "$500-750k"), (750e3, 1e12, "$750k+")]
AREAS = ["Minneapolis", "Saint Paul", "Hennepin County", "Ramsey County", "Anoka County", "Dakota County", "Washington County", "Scott County", "Carver County"]


def run_of(history, sold_date):
    """This listing run before `sold_date`: (first listed date, original ask, last ask, cuts, relisted)."""
    t = sold_date[:10]
    prev = max((e[0][:10] for e in history if e[0] and e[0][:10] < t and (e[1] or "").lower() == "sold"), default="")
    ev = sorted((e for e in history if e[0] and prev < e[0][:10] < t and e[2] and e[2] >= 20000
                 and (e[1] or "").lower() in ("listed", "relisted", "price changed")), key=lambda e: e[0])
    if not ev:
        return None
    starts = [i for i, e in enumerate(ev) if (e[1] or "").lower() in ("listed", "relisted")]
    s0 = starts[-1] if starts else 0
    run = ev[s0:]
    prices = [e[2] for e in run]
    return dict(listed=run[0][0][:10], orig=prices[0], ask=prices[-1], n_cuts=sum(1 for a, b in zip(prices, prices[1:]) if b < a),
                relisted=(run[0][1] or "").lower() == "relisted" or len(starts) > 1)


def sl_inputs(ask, worth, dom, n_cuts, orig, relisted, type_group, area, month, cond_effect, hoa):
    return dict(ask=ask, worth=worth, dom=dom, n_cuts=n_cuts or 0, cut=max(0.0, 1 - ask / orig) if orig else 0.0, relisted=bool(relisted),
                type_group=type_group, area=area, month=int(month), cond=cond_effect or 0.0, hoa=hoa or 0)


class SaleListModel:
    def __init__(self, cols, beta, n, rmse, shrink=1.0, shift=0.0):
        self.cols, self.beta, self.n, self.rmse, self.shrink, self.shift = cols, list(beta), n, rmse, shrink, shift

    @staticmethod
    def _x(f):
        g = min(max(math.log(f["ask"] / f["worth"]), -0.3), 0.3) if f.get("worth") else 0.0
        x = {"intercept": 1.0, "ask vs records value (log, ±0.3)": g,
             "no records value": 0.0 if f.get("worth") else 1.0,
             "price cuts so far (share of first ask)": min(f["cut"], 0.5), "relisted": float(f["relisted"]),
             "needs-work words": 1.0 if f["cond"] < 0 else 0.0, "renovated words": 1.0 if f["cond"] > 0 else 0.0,
             "HOA $300+/mo": 1.0 if f["hoa"] >= 300 else 0.0}
        for lo, hi, lab in DOM_BINS[1:]:
            x[f"listed {lab}"] = 1.0 if f["dom"] is not None and lo <= f["dom"] < hi else 0.0
        x["1 price cut"] = 1.0 if f["n_cuts"] == 1 else 0.0
        x["2+ price cuts"] = 1.0 if f["n_cuts"] >= 2 else 0.0
        for lo, hi, lab in BANDS:
            if lab != "$350-500k":
                x[f"ask {lab}"] = 1.0 if lo <= f["ask"] < hi else 0.0
        x["townhome"] = 1.0 if f["type_group"] == "townhome" else 0.0
        x["condo"] = 1.0 if f["type_group"] == "condo" else 0.0
        for a in AREAS[1:]:
            x[a] = 1.0 if f["area"] == a else 0.0
        q = (f["month"] - 1) // 3
        for qq, lab in ((1, "spring (Apr-Jun)"), (2, "summer (Jul-Sep)"), (3, "fall (Oct-Dec)")):
            x[lab] = 1.0 if q == qq else 0.0
        return x

    @classmethod
    def fit(cls, rows):
        """rows: (inputs dict, sale price)."""
        X = [cls._x(f) for f, _ in rows]
        cols = list(X[0])
        A = np.array([[x[c] for c in cols] for x in X])
        y = np.array([math.log(p / f["ask"]) for f, p in rows])
        keep = np.ones(len(y), bool)
        for _ in range(2):  # trim the worst 1%: a sale of a different unit, a typo'd price
            b = np.linalg.lstsq(A[keep] + 0, y[keep], rcond=None)[0]
            r = np.abs(y - A @ b)
            keep = r <= np.quantile(r, 0.99)
        return cls(cols, b, int(keep.sum()), float(np.sqrt(np.mean((y[keep] - A[keep] @ b) ** 2))))

    def ratio(self, f):
        x = self._x(f)
        return math.exp(self.shift + self.shrink * self.raw(f))

    def raw(self, f):
        x = self._x(f)
        return sum(b * x[c] for c, b in zip(self.cols, self.beta))

    def summary(self):
        return {"n": self.n, "rmse_log": round(self.rmse, 4), "close_lag_days": CLOSE_LAG, "shrink": self.shrink, "shift": self.shift,
                "coefficients": {c: round(b, 4) for c, b in zip(self.cols, self.beta)},
                "as_percent": {c: round(math.exp(b) - 1, 4) for c, b in zip(self.cols, self.beta) if c != "intercept"}}

    @classmethod
    def load(cls, path=None):
        f = Path(path or D / "sale_list_model.json")
        if not f.exists():
            return None
        j = json.loads(f.read_text())
        return cls(list(j["coefficients"]), list(j["coefficients"].values()), j["n"], j["rmse_log"], j.get("shrink", 1.0), j.get("shift", 0.0))
