#!/usr/bin/env python3
"""Every public record we hold on each ranked listing's parcel, in plain words.

    python scripts/build_records.py     # reads data/index.parquet + bricks closed_system.duckdb, writes site/records.json

One entry per listing: `tags` (two to four short labels for the row) and `items`
(dated lines, newest first). Joined on parcel (county:pin), never on address.
A code we can't decode with confidence is left out rather than guessed.
"""
import datetime as dt, json, re
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parent.parent
BRICKS = Path.home() / "Desktop/bricks/data"
con = duckdb.connect()
con.execute(f"attach '{BRICKS / 'closed_system.duckdb'}' as cs (read_only)")
con.execute(f"create table L as select property_id, county || ':' || pin as pk from '{ROOT / 'data/index.parquet'}' where rank_ok and pin is not null")

def q(sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]

def ms(v):
    return None if v in (None, "") else dt.datetime.fromtimestamp(int(v) / 1000, dt.UTC).date().isoformat()

def money(v):
    return f"${float(v):,.0f}"

def title(s):
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s[:1].upper() + s[1:].lower() if s.isupper() else s

out = {}
def add(pid, date, text, tag=None):
    r = out.setdefault(pid, {"tags": [], "items": []})
    r["items"].append({"date": date, "text": text})
    if tag and tag not in r["tags"]:
        r["tags"].append(tag)

def rows(layer, extra=""):
    return q(f"select L.property_id pid, r.event_at, r.attrs from cs.record r join L on r.parcel_key = L.pk where r.layer = '{layer}' {extra}")

# Minneapolis code-enforcement cases: one line per case, its inspections rolled up.
CASE = {"Nuisance": "Nuisance case", "VBR": "Vacant building registration", "HIS": "Housing inspection",
        "FIS": "Fire inspection", "Hazmat": "Hazardous materials case"}
GROUP = {"HISRENTAL": "Rental housing inspection", "FISRENTAL": "Rental fire inspection", "CVBR": "Condemned vacant building"}
cases = {}
for r in rows("mpls_caseinspections"):
    a = json.loads(r["attrs"])
    cases.setdefault((r["pid"], a["Violation_Case_Number"]), []).append(a)
for (pid, num), insp in cases.items():
    a0 = insp[0]
    kind = GROUP.get(a0.get("Case_Group")) or CASE.get(a0.get("Case_Type"), "City case")
    dates = sorted(d for d in (ms(i.get("Completed_Date")) for i in insp) if d and d <= "2026-12-31")
    if not dates:
        continue
    res = [i.get("Inspection_Result") for i in insp]
    bits = [f"{len(insp)} inspection{'s' if len(insp) > 1 else ''}"]
    if any(x in ("ConductCit", "AdminCit") for x in res): bits.append("citation issued")
    elif any(x in ("ConductWar", "AdminWarn") for x in res): bits.append("warning issued")
    if any("Condemnation" in (i.get("Inspection_Type_Desc") or "") for i in insp): bits.append("reinspected after condemnation")
    if "Final" in res: bits.append("closed")
    span = dates[0][:7] if dates[0][:7] == dates[-1][:7] else f"{dates[0][:7]} to {dates[-1][:7]}"
    add(pid, dates[-1], f"{kind} {num}: {', '.join(bits)} ({span})",
        {"Nuisance case": "Nuisance case", "Vacant building registration": "Registered vacant",
         "Condemned vacant building": "Condemned"}.get(kind, "Rental license" if "Rental" in kind else None))

for r in rows("mpls_pw_specialassessments"):
    a = json.loads(r["attrs"])
    if a.get("TotalAssessment"):
        add(r["pid"], None, f"Minneapolis special assessment of {money(a['TotalAssessment'])} on the tax bill (project {a.get('ProjectNumber')})", "Special assessment")

for r in rows("mpls_structural_fires"):
    a = json.loads(r["attrs"])
    loss = (a.get("propertyLoss") or 0) + (a.get("contentLoss") or 0)
    add(r["pid"], ms(a.get("alarmDate")), f"{title(a.get('incidentType'))}: {title(a.get('propertyUse'))}" + (f", {money(loss)} loss" if loss else ""), "Past fire")

for r in rows("mpls_damagedproperties"):
    a = json.loads(r["attrs"])
    add(r["pid"], "2020-05-31", f"Recorded as damaged in May 2020 ({(a.get('typeOfDamage') or '').replace('_', ' ')})", None)

VB = {"1": "category 1: empty but secure", "2": "category 2: needs a code compliance inspection before anyone lives there",
      "3": "category 3: condemned or a nuisance"}
for r in rows("stp_vacant_buildings"):
    a = json.loads(r["attrs"])
    d = a.get("VACANT_AS_OF")
    iso = f"{d[6:10]}-{d[0:2]}-{d[3:5]}" if d and len(d) == 10 else None
    add(r["pid"], iso, f"On Saint Paul's vacant building list, {VB.get(str(a.get('VB_CATEGORY')), 'category ' + str(a.get('VB_CATEGORY')))}",
        f"Vacant, category {a.get('VB_CATEGORY')}")

comp = {}
for r in rows("stp_resident_service_requests", "and r.event_at >= date '2023-01-01'"):
    a = json.loads(r["attrs"])
    comp.setdefault(r["pid"], []).append((ms(a.get("REQUEST_DATE")), a.get("REQUEST_DESCRIPTION")))
for pid, c in comp.items():
    c.sort(reverse=True)
    kinds = {}
    for _, k in c: kinds[k] = kinds.get(k, 0) + 1
    add(pid, c[0][0], f"{len(c)} city complaint{'s' if len(c) > 1 else ''} since 2023: " + ", ".join(f"{k}" + (f" ×{n}" if n > 1 else "") for k, n in kinds.items()),
        f"{len(c)} complaints" if len(c) >= 3 else None)

for r in rows("stp_certificate_of_occupancy___residential"):
    a = json.loads(r["attrs"])
    g = (a.get("GRADE") or "").strip()
    if g in "ABCD" and g:
        add(r["pid"], None, f"Licensed rental, {a.get('UNITS')} unit{'s' if (a.get('UNITS') or 0) != 1 else ''}; Saint Paul inspection grade {g} (A is best, D worst)", f"Rental, grade {g}")

for layer, what in (("stp_building_permits_", "Permit"), ("stp_permit_tppe_demolition_permit", "Demolition permit")):
    for r in rows(layer, "and r.event_at >= date '2020-01-01'"):
        a = json.loads(r["attrs"])
        v = a.get("EST_VALUE_OF_WORK") or a.get("STATE_VALUATION")
        desc = title(a.get("FOLDERDESCRIPTION") or a.get("WORK_TYPE"))
        add(r["pid"], ms(a.get("ISSUEDATE")), f"{what}: {desc}" + (f" ({money(v)})" if v else "") + f", {(a.get('STATUS') or '').lower()}",
            "Demolition permit" if what.startswith("Demo") else None)

for r in rows("fema_flood_zones", "and json_extract_string(r.attrs, 'SFHA_TF') = 'T'"):
    a = json.loads(r["attrs"])
    add(r["pid"], None, f"In a FEMA flood zone ({a.get('FLD_ZONE')}); a lender will require flood insurance", "Flood zone")

# Minneapolis + Saint Paul permits already on the index row.
for r in q(f"select property_id pid, unnest(permits) p from '{ROOT / 'data/index.parquet'}' where rank_ok and permits is not null"):
    p = r["p"]
    if p.get("issued"):
        add(r["pid"], str(p["issued"]), f"Permit: {title(p.get('what') or p.get('work'))}" + (f" ({money(p['value'])})" if p.get("value") else "") + f", {(p.get('status') or '').lower()}")

for v in out.values():
    seen, items = set(), []
    for i in sorted(v["items"], key=lambda i: i["date"] or "", reverse=True):
        if i["text"] not in seen:
            seen.add(i["text"]); items.append(i)
    v["items"] = items
(ROOT / "site/records.json").write_text(json.dumps(out, separators=(",", ":")))
print(len(out), "listings with records;", sum(len(v["items"]) for v in out.values()), "lines")
