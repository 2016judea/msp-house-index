#!/usr/bin/env python3
"""Each ranked home's history, as records: the county's value by year, every recorded
sale, every listing event, every permit. The page draws the chart and writes the story
from these with templates; nothing here is written by a model.

    python scripts/build_timeline.py   # after build_index.py; writes private/tl/<00-99>.json

Sources (all joined on the county parcel, never on address):
  assess   bricks parcel_snapshot: MetroGIS 2021-current, Minneapolis assessor 2017-2025,
           county pulls 2026-08. Year = the snapshot's MKT_YEAR; where the county left it
           blank (Hennepin), Minneapolis assessor files carry their own year and MetroGIS
           release Y carries year Y-1 (they match value for value).
  sales    bricks `sale` (every deed the snapshots ever showed) + Realtor's MLS "Sold"
           events. A deed whose price was also recorded on other parcels the same day is
           a bulk deed: kept, marked, never read as this house's price.
  listing  Realtor property_history (NorthstarMLS): listed, price changes, removed, relisted
  permits  Saint Paul stp_permits (all trades, with EST_VALUE_OF_WORK, through 2025-06-30),
           Minneapolis mpls_permits (2016-) + data/mpls_permits.jsonl (2024-, with the comment)
Sharded by the last two digits of property_id so an opened row loads ~1% of the file.
"""
import datetime as dt, json, re
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
BRICKS = Path.home() / "Desktop/bricks/data"
c = duckdb.connect()
c.execute(f"attach '{BRICKS / 'closed_system.duckdb'}' as cs (read_only)")
c.execute(f"create table L as select property_id, county || ':' || pin as pk, county from '{D / 'index.parquet'}' where rank_ok and pin is not null")


def q(sql):
    cur = c.execute(sql)
    cols = [x[0] for x in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def iso(x):
    return None if x is None else str(x)[:10]


T = {}
def get(pid):
    return T.setdefault(pid, {"assess": {}, "sales": [], "list": [], "permits": []})

# --- county value by year ---------------------------------------------------
PRI = {"minneapolis_assessor": 0, "county": 1, "metrogis": 2}
for r in q("""select L.property_id pid, s.vintage, s.mkt_year, s.emv_total from cs.parcel_snapshot s join L on s.parcel_key = L.pk
              where s.emv_total > 0"""):
    v = r["vintage"]
    m = re.match(r"(minneapolis_assessor|metrogis|county)_(?:\w+_)?(\d{4}|current)", v)
    if not m:
        continue
    src, y = m.groups()
    yr = r["mkt_year"]
    if not yr:
        if src == "minneapolis_assessor":
            yr = int(y)
        elif src == "metrogis" and y != "current":
            yr = int(y) - 1
        else:
            continue  # current/county pulls with no year: the same value as the newest dated file
    a = get(r["pid"])["assess"]
    if yr not in a or PRI[src] < a[yr][1]:
        a[yr] = (round(r["emv_total"]), PRI[src])
# 'current' rows with no MKT_YEAR (Hennepin suburbs): the year after the newest MetroGIS release.
for r in q("""select L.property_id pid, s.emv_total from cs.parcel_snapshot s join L on s.parcel_key = L.pk
              where s.vintage = 'metrogis_current' and s.mkt_year is null and s.emv_total > 0"""):
    a = get(r["pid"])["assess"]
    if a:
        y = max(a) + 1
        if y <= dt.date.today().year and a[max(a)][0] != round(r["emv_total"]):
            a[y] = (round(r["emv_total"]), 2)

# --- recorded sales -------------------------------------------------------
c.execute("""create table S as select s.parcel_key, s.sale_date, s.sale_value from cs.sale s where s.parcel_key in (select pk from L) and s.sale_value >= 1000""")
c.execute("""create table bulk as select split_part(s.parcel_key, ':', 1) co, s.sale_date, s.sale_value, count(distinct s.parcel_key) n
             from cs.sale s join (select distinct split_part(parcel_key, ':', 1) co, sale_date, sale_value from S) k
               on split_part(s.parcel_key, ':', 1) = k.co and s.sale_date = k.sale_date and s.sale_value = k.sale_value
             where k.co <> 'hennepin'   -- Hennepin deed dates are months (every 'day' row is the 1st), so a shared date+price is coincidence
             group by all having count(distinct s.parcel_key) > 1""")
for r in q("""select L.property_id pid, S.sale_date d, S.sale_value v, b.n from S join L on S.parcel_key = L.pk
              left join bulk b on b.co = L.county and b.sale_date = S.sale_date and b.sale_value = S.sale_value
              order by 1, 2"""):
    ss = get(r["pid"])["sales"]
    d = iso(r["d"])
    if any(abs((dt.date.fromisoformat(x["d"]) - r["d"]).days) < 45 and abs(x["v"] - r["v"]) <= 0.01 * r["v"] for x in ss):
        continue  # Hennepin repeats a deed at month grain
    ss.append({"d": d, "v": round(r["v"]), "src": "county deed", **({"bulk": r["n"]} if r["n"] else {})})

# --- listing events + MLS sales (Realtor) ----------------------------------
want = {r[0] for r in c.execute("select property_id from L").fetchall()}
for line in open(D / "details.jsonl"):
    try:
        det = json.loads(line)
    except json.JSONDecodeError:
        continue
    pid = det["property_id"]
    if pid not in want:
        continue
    t = get(pid)
    for e in det.get("property_history") or []:
        name, d, p = (e.get("event_name") or ""), iso(e.get("date")), e.get("price") or 0
        if not d:
            continue
        if name.lower() == "sold":
            # The MLS saw this house sell at this price: a "bulk" deed at the same price was a coincidence.
            for x in t["sales"]:
                if p and x.get("bulk") and abs(x["v"] - p) <= 0.01 * p and abs((dt.date.fromisoformat(x["d"]) - dt.date.fromisoformat(d)).days) < 200:
                    x.pop("bulk")
            if p and not any(abs((dt.date.fromisoformat(x["d"]) - dt.date.fromisoformat(d)).days) < 200 and abs(x["v"] - p) <= 0.02 * p for x in t["sales"]):
                t["sales"].append({"d": d, "v": round(p), "src": e.get("source_name") or "MLS"})
        elif name in ("Listed", "Relisted", "Price Changed", "Listing removed", "Pending", "Contingent"):
            t["list"].append({"d": d, "e": name, "p": round(p) if p else None})

# --- permits ---------------------------------------------------------------
for r in q("""select L.property_id pid, r.attrs from cs.record r join L on r.parcel_key = L.pk where r.layer = 'stp_permits'"""):
    a = json.loads(r["attrs"])
    d = dt.datetime.fromtimestamp(int(a["ISSUEDATE"]) / 1000, dt.UTC).date().isoformat() if a.get("ISSUEDATE") else None
    if not d:
        continue
    what = (a.get("FOLDER_TYPE") or "").replace("Plumbing/Gasfitting/Inside Water Piping", "Plumbing").replace("Warm Air, Ventilation & General Sheet", "Heating and ventilation").strip()
    get(r["pid"])["permits"].append({"d": d, "num": a.get("PERMITNUMBER"), "what": what, "work": (a.get("WORK_TYPE") or "").strip(),
                                     "desc": re.sub(r"\s+", " ", (a.get("FOLDERDESCRIPTION") or "")).strip()[:140],
                                     "v": round(float(a["EST_VALUE_OF_WORK"])) if a.get("EST_VALUE_OF_WORK") else None, "status": a.get("STATUS")})
mpls_c = {}
for line in open(D / "mpls_permits.jsonl"):
    a = json.loads(line)
    mpls_c[a.get("permitNumber")] = (a.get("comments") or "").strip()
for r in q("""select L.property_id pid, r.attrs from cs.record r join L on r.parcel_key = L.pk where r.layer = 'mpls_permits'"""):
    a = json.loads(r["attrs"])
    if not a.get("issueDate"):
        continue
    d = dt.datetime.fromtimestamp(int(a["issueDate"]) / 1000, dt.UTC).date().isoformat()
    get(r["pid"])["permits"].append({"d": d, "num": a.get("permitNumber"), "what": a.get("permitType") or "", "work": "",
                                     "desc": re.sub(r"\s+", " ", mpls_c.get(a.get("permitNumber"), "")).strip()[:140], "v": None, "status": a.get("status")})
for t in T.values():
    seen, ps = set(), []
    for p in sorted(t["permits"], key=lambda p: p["d"]):
        if p["num"] and p["num"] in seen:
            continue
        seen.add(p["num"]); ps.append({k: v for k, v in p.items() if v not in (None, "")})
    t["permits"] = ps
    t["assess"] = sorted([y, v[0]] for y, v in t["assess"].items())
    t["sales"].sort(key=lambda s: s["d"])
    t["list"].sort(key=lambda e: e["d"])

out = ROOT / "private/tl"
out.mkdir(parents=True, exist_ok=True)
shards = {}
for pid, t in T.items():
    shards.setdefault(pid[-2:], {})[pid] = t
for k, v in shards.items():
    (out / f"{k}.json").write_text(json.dumps(v, separators=(",", ":")))
n = len(T)
print(f"{n} homes; assess {sum(bool(t['assess']) for t in T.values())}, sales {sum(len(t['sales']) for t in T.values())}, "
      f"listing events {sum(len(t['list']) for t in T.values())}, permits {sum(len(t['permits']) for t in T.values())}; "
      f"{len(shards)} shards, {sum(f.stat().st_size for f in out.glob('*.json')) / 1e6:.1f} MB")
