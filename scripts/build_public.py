#!/usr/bin/env python3
"""Gate the data: site/ ships the top 10 only; the full set goes to the paid route.

    python scripts/build_public.py        # last step of `make build`

Reads the full set the other build steps write to private/ (gitignored):
  private/data.json, evidence.json, records.json, tl/<00-99>.json, and any other
  per-listing private/*.json keyed by property_id (map data, etc.).

Writes:
  site/data.json, evidence.json, records.json, tl/   the 10 furthest below value (the page's
                                                     default sort) and nothing else
  site/meta.json                                     the count and every city, for the paywall copy
  private/bundle/rows.json.gz                        the full data.json, gzipped, served by /api/data
  private/bundle/detail.json.gz                      {property_id: {ev, rec, tl, ...}} for /api/data?id=
  private/bundle/map/<0-63>.json.gz                  map cells, packed, for /api/data?cell=  (site/map/ keeps the top 10's)

scripts/deploy.py uploads private/bundle/ as api/_full/, readable by the function only.
"""
import collections, gzip, json, math, os, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIV, SITE = ROOT / "private", Path(os.environ.get("PUBLIC_OUT", ROOT / "site"))
TOP = 10
dump = lambda o: json.dumps(o, separators=(",", ":"), default=str)

d = json.loads((PRIV / "data.json").read_text())
rows = sorted(d["rows"], key=lambda r: r["rank"])


# The page's default sort: "Room", the predicted discount off the ask (room_pct, from build_index.py),
# mirrored in site/index.html under(): Math.round(room_pct*100), stable over rank order.
def under(r):
    return None if r.get("room_pct") is None else math.floor(r["room_pct"] * 100 + 0.5)


# The free ten are a SHOWCASE, not the raw top of the sort (Aidan 2026-10-12: "a diverser / more
# interesting 10 ... lets showcase our depth"). The raw top was nine condos. Each slot below picks the
# listing with the most room among those that show off one layer of the product, preferring homes
# whose records, story and neighbourhood are richest; no more than two from one city.
def _depth_inputs():
    rec = json.loads((PRIV / "records.json").read_text())
    tl = {}
    for f in (PRIV / "tl").glob("*.json"):
        tl.update(json.loads(f.read_text()))
    nb = {}
    for f in (PRIV / "nb").glob("*.json"):
        nb[f.stem] = json.loads(f.read_text()).get("lines", [])
    comps = json.loads((PRIV / "comps.json").read_text())
    return rec, tl, nb, comps

_rec, _tl, _nb, _comps = _depth_inputs()

def depth(r):
    pid = r["property_id"]
    t = _tl.get(pid, {})
    story = len(t.get("assess", [])) + 2 * len(t.get("sales", [])) + len(t.get("list", []))
    recs = 3 * len(_rec.get(pid, {}).get("items", []))
    nbl = _nb.get(pid, [])
    nbs = len(nbl) + 2 * sum(1 for l in nbl if l.get("tone") == "amber")
    return story + recs + nbs + (3 if _comps.get(pid, {}).get("comps") else 0)

def rtext(r):
    return " ".join(i["text"] for i in _rec.get(r["property_id"], {}).get("items", [])).lower()

def nbtext(r):
    return " ".join(l.get("text", "") for l in _nb.get(r["property_id"], [])).lower()

ranked = [r for r in rows if under(r) is not None]
SLOTS = [   # the record-rich slots first, so the city cap doesn't starve them
    ("Minneapolis code violations on record", lambda r: r["city"] == "Minneapolis" and "violation" in rtext(r) and under(r) > 0),
    ("Saint Paul vacant list or complaints", lambda r: r["city"] == "Saint Paul" and ("vacant" in rtext(r) or "complaint" in rtext(r)) and under(r) > 0),
    ("needs work, valued as-is", lambda r: r.get("tier") in ("heavy", "condemned") and under(r) > 0),
    ("historic or a cleanup site nearby", lambda r: ("historic" in nbtext(r) or "mpca" in nbtext(r)) and under(r) > 0),
    ("most room, a house", lambda r: r["type_group"] == "house" and under(r) > 0),
    ("most room, a condo", lambda r: r["type_group"] == "condo" and under(r) > 0),
    ("a townhome with room", lambda r: r["type_group"] == "townhome" and under(r) > 0),
    ("a 2-4 unit building", lambda r: r["type_group"] == "multi" and under(r) > 0),
    ("a suburb, over $600k", lambda r: r["city"] not in ("Minneapolis", "Saint Paul") and r["ask"] >= 600000 and under(r) > 0),
    ("expect to pay over ask", lambda r: under(r) < 0),
]
top, used, per_city, per_type = [], set(), collections.Counter(), collections.Counter()
# A showcase row must be one the records can stand behind: the records value within 30% of the ask.
# (A $1.29M Columbia Heights condo whose records say ~$640k is a puzzle, not a showcase.)
def credible(r):
    w = r.get("worth_est") or r.get("arv")
    return bool(w) and 0.7 <= w / r["ask"] <= 1.3
fits = lambda r: r["property_id"] not in used and per_city[r["city"]] < 2 and per_type[r["type_group"]] < 3 and credible(r)
def take(r, name):
    r["showcase"] = name
    top.append(r); used.add(r["property_id"]); per_city[r["city"]] += 1; per_type[r["type_group"]] += 1
for name, ok in SLOTS:
    pool = [r for r in ranked if fits(r) and ok(r)]
    if not pool:
        continue
    # Room leads: keep the candidates within 2 points of the slot's best room (or most over ask),
    # then the one with the richest records, story and neighbourhood wins.
    sgn = -1 if name == "expect to pay over ask" else 1
    best = max(sgn * under(r) for r in pool)
    take(max((r for r in pool if sgn * under(r) >= best - 2), key=depth), name)
for r in sorted(ranked, key=under, reverse=True):   # fill any empty slot with the next by room
    if len(top) >= TOP: break
    if fits(r): take(r, "most room")
ids = {r["property_id"] for r in top}

ev = json.loads((PRIV / "evidence.json").read_text())
rec = json.loads((PRIV / "records.json").read_text())
tl = {}
for f in sorted((PRIV / "tl").glob("*.json")):
    tl.update(json.loads(f.read_text()))
# Other per-listing layers: a private/<name>.json keyed by property_id. Add the name here and
# the top 10's slice ships as site/<name>.json, every listing's goes behind /api/data?id=
# under the same key. (DATA_MODEL.md, "Gated per-listing data".)
PER_LISTING = []
extra = {n: json.loads((PRIV / f"{n}.json").read_text()) for n in PER_LISTING}
# Or a directory of one file per listing, private/<name>/<property_id>.json: the top 10's ship
# as site/<name>/<property_id>.json. The page reads either kind with layer(name, id).
PER_LISTING_DIRS = ["nb", "comps"]  # scripts/build_neighborhood.py; comps: build_index.py (msp/valuation.py grid)
for n in PER_LISTING_DIRS:
    extra[n] = {f.stem: json.loads(f.read_text()) for f in (PRIV / n).glob("*.json")}

# --- public: the top 10 ---
(SITE / "data.json").write_text(dump({**{k: v for k, v in d.items() if k != "rows"}, "rows": top}))
(SITE / "evidence.json").write_text(dump({i: ev[i] for i in ids if i in ev}))
(SITE / "records.json").write_text(dump({i: rec[i] for i in ids if i in rec}))
for name, o in extra.items():
    if name in PER_LISTING_DIRS:
        shutil.rmtree(SITE / name, ignore_errors=True)
        (SITE / name).mkdir()
        for i in ids & set(o):
            (SITE / name / f"{i}.json").write_text(dump(o[i]))
    else:
        (SITE / f"{name}.json").write_text(dump({i: o[i] for i in ids if i in o}))
shutil.rmtree(SITE / "tl", ignore_errors=True)
(SITE / "tl").mkdir()
shards = collections.defaultdict(dict)
for i in ids:
    if i in tl:
        shards[i[-2:]][i] = tl[i]
for k, v in shards.items():
    (SITE / "tl" / f"{k}.json").write_text(dump(v))
# The block map (scripts/build_map.py): cells of lots, keyed "<x>_<y>"; a row's m[0] lists its cells.
MAPS = 64
shard = lambda k: (int(k.split("_")[0]) * 31 + int(k.split("_")[1])) % MAPS  # mirrored in site/api/data.js
if (PRIV / "map").exists():
    shutil.rmtree(SITE / "map", ignore_errors=True)
    (SITE / "map").mkdir()
    for k in {k for r in top if r.get("m") for k in r["m"][0]}:
        shutil.copy(PRIV / "map" / f"{k}.json", SITE / "map" / f"{k}.json")
cities = [c for c, _ in collections.Counter(r["city"] for r in rows if r.get("city")).most_common()]
# The accuracy line, read from the backtest (scripts/backtest.py), never typed.
bt = json.loads((ROOT / "data" / "backtest.json").read_text())
sl = bt["sale_list"]
# The claim the backtest supports is the ORDER, not the point price: as a single number the
# prediction is no closer than the ask itself (2.2% vs 1.8% median error), but sorted into
# tenths by predicted room, the homes sold in that order. So the line states the tenths.
dec = sl["deciles"]
acc = {"n": sl["holdout_n"], "since": bt["holdout_from"], "median_abs_pct": sl["predicted"]["median_abs_pct"],
       "within_5": sl["predicted"]["within_5"], "within_2": sl["predicted"]["within_2"],
       "most_room_actual": dec[0]["actual_sale_vs_ask"], "least_room_actual": dec[-1]["actual_sale_vs_ask"]}
cg = json.loads((ROOT / "data" / "condition_phrases.json").read_text())["groups"]
method = {"sl_n": json.loads((ROOT / "data" / "sale_list_model.json").read_text())["n"], "groups": {g: v["effect"] for g, v in cg.items()}}
(SITE / "meta.json").write_text(dump({"built": d["built"], "n": len(rows), "shown": len(top), "cities": cities, "price_usd": 100, "accuracy": acc, "method": method}))

# --- private: the bundle the paid route serves ---
B = PRIV / "bundle"
B.mkdir(exist_ok=True)
(B / "rows.json.gz").write_bytes(gzip.compress(dump(d).encode(), 9))
detail = {}
for r in rows:
    i = r["property_id"]
    x = {"ev": ev.get(i), "rec": rec.get(i), "tl": tl.get(i)}
    for name, o in extra.items():
        x[name] = o.get(i)
    detail[i] = {k: v for k, v in x.items() if v is not None}
(B / "detail.json.gz").write_bytes(gzip.compress(dump(detail).encode(), 9))

if (PRIV / "map").exists():
    packs = collections.defaultdict(dict)
    for f in (PRIV / "map").glob("*.json"):
        packs[shard(f.stem)][f.stem] = json.loads(f.read_text())
    (B / "map").mkdir(exist_ok=True)
    for i, v in packs.items():
        (B / "map" / f"{i}.json.gz").write_bytes(gzip.compress(dump(v).encode(), 9))

pub = sum(f.stat().st_size for f in SITE.rglob("*.json"))
print(f"site/: {len(top)} of {len(rows)} homes, {len(cities)} cities, {pub / 1e3:.0f} KB of json"
      f" (+extra: {', '.join(extra) or 'none'}); bundle rows {(B / 'rows.json.gz').stat().st_size / 1e6:.1f} MB gz,"
      f" detail {(B / 'detail.json.gz').stat().st_size / 1e6:.1f} MB gz")
