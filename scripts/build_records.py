#!/usr/bin/env python3
"""Every public record we hold on each ranked listing's parcel, in plain words.

    python scripts/build_records.py     # reads data/index.parquet + bricks closed_system.duckdb, writes private/records.json

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
def add(pid, date, text, tag=None, means=None, detail=None, kind=None):
    r = out.setdefault(pid, {"tags": [], "items": []})
    it = {"date": date, "text": text}
    if kind: it["kind"] = kind
    if means: it["means"] = means
    if detail: it["detail"] = detail
    r["items"].append(it)
    if tag and tag not in r["tags"]:
        r["tags"].append(tag)

def rows(layer, extra=""):
    return q(f"select L.property_id pid, r.event_at, r.attrs from cs.record r join L on r.parcel_key = L.pk where r.layer = '{layer}' {extra}")

# Minneapolis code-enforcement cases. The violations (what was cited, live from the
# city's CaseViolations, scripts/fetch_city_records.py) and the inspections (when the
# inspector came, from bricks) are joined on the case number; one record per case.
CASE = {"Nuisance": "Nuisance case", "VBR": "Vacant building registration", "HIS": "Housing inspection",
        "FIS": "Fire inspection", "Hazmat": "Hazardous materials case"}
GROUP = {"HISRENTAL": "Rental housing inspection", "FISRENTAL": "Rental fire inspection", "CVBR": "Condemned vacant building"}
MEANS = {
 "Nuisance case": "Someone reported the outside of the property (trash, junk, tall grass, an inoperable car, an open building) and the city sent an inspector. If the owner doesn't fix it, the city can do the work and add the cost to the property taxes.",
 "Vacant building registration": "Minneapolis makes owners of empty buildings register them and pay a yearly fee while the city monitors them.",
 "Condemned vacant building": "The city condemned the building as unfit to live in. Nobody can live there until it passes the city's inspection.",
 "Rental housing inspection": "The city inspects licensed rentals on a schedule. Anything cited has to be fixed by the owner.",
 "Housing inspection": "A city housing inspection. Anything cited has to be fixed by the owner.",
 "Rental fire inspection": "A fire-safety inspection of a rental. Anything cited has to be fixed by the owner.",
 "Fire inspection": "A fire-safety inspection. Anything cited has to be fixed by the owner.",
}
STAGE = {"Assessed": "the city did the work and billed it to the property taxes", "Intent to Assess": "the city intends to bill the work to the property taxes",
         "Pending Assessment": "a bill to the property taxes is pending", "Assessment Hearing": "at an assessment hearing",
         "Citation Tier 1 Issuance": "citation issued", "Citation Tier 2 Issuance": "second-tier citation issued",
         "Authorization": "the city authorized doing the work itself", "Reinspection": "waiting on a reinspection",
         "Case Closed": "closed", "Closed": "closed", "Case Cancelled": "cancelled"}
RESULT = {"Final": "final, passed", "ConductCit": "citation issued", "AdminCit": "citation issued", "ConductWar": "warning issued",
          "AdminWarn": "warning issued", "Cancel": "cancelled"}
viol = {}
for line in open(ROOT / "data/mpls_violations.jsonl"):
    v = json.loads(line)
    viol.setdefault(v["Violation_Case_Number"], []).append(v)
insp = {}
for r in rows("mpls_caseinspections"):
    a = json.loads(r["attrs"])
    insp.setdefault((r["pid"], a["Violation_Case_Number"]), []).append(a)
pin_pid = {pk.split(":")[1]: pid for pid, pk in con.execute("select property_id, pk from L where pk like 'hennepin:%'").fetchall()}
for num, vs in viol.items():
    pid = pin_pid.get(vs[0]["APN"])
    if pid and (pid, num) not in insp:
        insp[(pid, num)] = []
for (pid, num), ins in insp.items():
    vs = viol.get(num, [])
    a0 = (vs or ins)[0]
    kind = GROUP.get(a0.get("Case_Group")) or CASE.get(a0.get("Case_Type"), "City case")
    idates = sorted(d for d in (ms(i.get("Completed_Date")) for i in ins) if d and d <= "2026-12-31")
    vdates = sorted(d for d in (ms(v.get("Processed_Date")) for v in vs) if d)
    dates = sorted(idates + vdates)
    if not dates:
        continue
    stage = STAGE.get((vs[0].get("Milestone_Violation_Case") if vs else "") or "", "")
    cited = [v for v in vs if (v.get("Violation_Code_Description") or "").strip()]
    head = f"{kind} {num}"
    if cited:
        head += f": {len(cited)} violation{'s' if len(cited) > 1 else ''} cited"
    if stage:
        head += f", {stage}"
    detail = []
    for v in sorted(cited, key=lambda v: v.get("Processed_Date") or 0):
        fixed = ms(v.get("Resolved_Date"))
        st = (v.get("Status_Violation") or "").strip()
        detail.append(f"Cited: {v['Violation_Code_Description'].strip()}" + (f" ({v['Violation_Code']})" if v.get("Violation_Code") else "")
                      + (f". Fixed {fixed}" if fixed and st == "Abate" else ". Cancelled" if st == "Cancel" else ". Not fixed yet" if not fixed else ""))
    for i in sorted(ins, key=lambda i: i.get("Completed_Date") or 0):
        d = ms(i.get("Completed_Date"))
        if d and d <= "2026-12-31":
            res = RESULT.get(i.get("Inspection_Result"))
            detail.append(f"{d}: {i.get('Inspection_Type_Desc') or 'Inspection'}" + (f", {res}" if res else ""))
    add(pid, dates[-1], head, {"Nuisance case": "Nuisance case", "Vacant building registration": "Registered vacant",
        "Condemned vacant building": "Condemned"}.get(kind, "Rental license" if "Rental" in kind else None),
        means=MEANS.get(kind), detail=detail, kind="case")
    if stage.startswith("the city did the work"):
        out[pid]["tags"].append("City billed work to taxes") if "City billed work to taxes" not in out[pid]["tags"] else None

# Minneapolis 311 complaints about the property. The city geocodes a 311 point onto the
# street, 20-30 m from the house, so a fixed radius catches the neighbours too. Instead each
# complaint goes to its NEAREST parcel (within 45 m) in the county parcel file, and counts for
# a listing only when that nearest parcel is the listing's own.
con.execute(f"attach '{ROOT / 'data/county.duckdb'}' as cty (read_only)")
con.execute(f"""create table C as select row_number() over () as cid, * from read_json_auto('{ROOT / 'data/mpls_311.jsonl'}') where lat > 44 and lon < -92""")
con.execute("""create table NP as
  select C.cid, arg_min(p.pin, (p.lat - C.lat)^2 + ((p.lon - C.lon) * 0.707)^2) as pin
  from C join cty.parcels p on p.county = 'hennepin' and abs(p.lat - C.lat) < 0.0004 and abs(p.lon - C.lon) < 0.00057
  where p.is_dwelling group by 1
  having min((p.lat - C.lat)^2 + ((p.lon - C.lon) * 0.707)^2) < (45 / 111000.0)^2""")
for pid, cnt, items in con.execute("""
  select L.property_id, count(*), list({'d': C.OPENEDDATETIME, 't': C.TYPENAME, 'closed': C.CLOSEDDATETIME} order by C.OPENEDDATETIME desc)
  from L join NP on L.pk = 'hennepin:' || NP.pin join C using (cid) group by 1""").fetchall():
    kinds = {}
    for it in items: kinds[it["t"]] = kinds.get(it["t"], 0) + 1
    add(pid, ms(items[0]["d"]), f"{cnt} complaint{'s' if cnt > 1 else ''} to Minneapolis 311 since 2023: " + ", ".join(k + (f" ×{n}" if n > 1 else "") for k, n in kinds.items()),
        f"{cnt} complaints" if cnt >= 3 else None,
        means="Calls or online reports to the city placed at this lot (the city maps each one to the street, so it is matched to the nearest house). Each is a report, not a finding. Minneapolis publishes the kind of complaint, not what the caller said.",
        detail=[f"{ms(it['d'])}: {it['t']}" + ("" if it["closed"] else " (still open)") for it in items], kind="complaints")
    out[pid].setdefault("ev", []).extend({"d": ms(it["d"]), "t": it["t"], "src": "Minneapolis 311", "open": not it["closed"]} for it in items)

VB = {"1": "category 1: empty but secure", "2": "category 2: needs a code compliance inspection before anyone lives there",
      "3": "category 3: condemned or a nuisance"}
for r in rows("stp_vacant_buildings"):
    a = json.loads(r["attrs"])
    d = a.get("VACANT_AS_OF")
    iso = f"{d[6:10]}-{d[0:2]}-{d[3:5]}" if d and len(d) == 10 else None
    add(r["pid"], iso, f"On Saint Paul's vacant building list, {VB.get(str(a.get('VB_CATEGORY')), 'category ' + str(a.get('VB_CATEGORY')))}",
        f"Vacant, category {a.get('VB_CATEGORY')}",
        means="Saint Paul tracks every empty building. Category 1 is empty but secure. Category 2 needs a code compliance inspection, and the work it finds, before anyone can live there. Category 3 is condemned or a nuisance.",
        detail=[f"Dwelling type: {a.get('DWELLING_TYPE')}", f"Vacant as of {d}", f"Ward {a.get('WARD')}"], kind="vacant")

comp = {}
for r in rows("stp_resident_service_requests", "and r.event_at >= date '2023-01-01'"):
    a = json.loads(r["attrs"])
    comp.setdefault(r["pid"], []).append((ms(a.get("REQUEST_DATE")), a.get("REQUEST_DESCRIPTION")))
for pid, c in comp.items():
    c.sort(reverse=True)
    kinds = {}
    for _, k in c: kinds[k] = kinds.get(k, 0) + 1
    add(pid, c[0][0], f"{len(c)} city complaint{'s' if len(c) > 1 else ''} since 2023: " + ", ".join(f"{k}" + (f" ×{n}" if n > 1 else "") for k, n in kinds.items()),
        f"{len(c)} complaints" if len(c) >= 3 else None,
        means="Requests and complaints to Saint Paul about this property. Each is a report, not a finding. Saint Paul publishes the kind of complaint, not its text.",
        detail=[f"{d}: {k}" for d, k in c], kind="complaints")
    out[pid].setdefault("ev", []).extend({"d": d, "t": k, "src": "Saint Paul"} for d, k in c)

for r in rows("stp_certificate_of_occupancy___residential"):
    a = json.loads(r["attrs"])
    g = (a.get("GRADE") or "").strip()
    if g in "ABCD" and g:
        add(r["pid"], None, f"Licensed rental, {a.get('UNITS')} unit{'s' if (a.get('UNITS') or 0) != 1 else ''}; Saint Paul inspection grade {g} (A is best, D worst)", f"Rental, grade {g}",
            means="Saint Paul licenses rentals and grades them A to D from what its inspections find. A grade sets how often the city comes back.",
            detail=[f"Status: {a.get('STATUS')}", f"Type: {a.get('SUB_TYPE')}"] + ([f"Renewal due {ms(a.get('RENEWAL_DUE'))}"] if a.get("RENEWAL_DUE") else []), kind="rental")

for layer, what in (("stp_building_permits_", "Permit"), ("stp_permit_tppe_demolition_permit", "Demolition permit")):
    for r in rows(layer, "and r.event_at >= date '2020-01-01'"):
        a = json.loads(r["attrs"])
        v = a.get("EST_VALUE_OF_WORK") or a.get("STATE_VALUATION")
        desc = title(a.get("FOLDERDESCRIPTION") or a.get("WORK_TYPE"))
        add(r["pid"], ms(a.get("ISSUEDATE")), f"{what}: {desc}" + (f" ({money(v)})" if v else "") + f", {(a.get('STATUS') or '').lower()}",
            "Demolition permit" if what.startswith("Demo") else None, kind="permit")

for r in rows("fema_flood_zones", "and json_extract_string(r.attrs, 'SFHA_TF') = 'T'"):
    a = json.loads(r["attrs"])
    add(r["pid"], None, f"In a FEMA flood zone ({a.get('FLD_ZONE')}); a lender will require flood insurance", "Flood zone",
            means="FEMA maps this parcel inside a 1%-a-year flood area. Any mortgage on it will require flood insurance.", kind="flood")

# Minneapolis + Saint Paul permits already on the index row.
for r in q(f"select property_id pid, unnest(permits) p from '{ROOT / 'data/index.parquet'}' where rank_ok and permits is not null"):
    p = r["p"]
    if p.get("issued"):
        add(r["pid"], str(p["issued"]), f"Permit: {title(p.get('what') or p.get('work'))}" + (f" ({money(p['value'])})" if p.get("value") else "") + f", {(p.get('status') or '').lower()}", kind="permit")

for v in out.values():
    seen, items = set(), []
    for i in sorted(v["items"], key=lambda i: i["date"] or "", reverse=True):
        if i["text"] not in seen:
            seen.add(i["text"]); items.append(i)
    v["items"] = items
(ROOT / "private/records.json").write_text(json.dumps(out, separators=(",", ":")))
print(len(out), "listings with records;", sum(len(v["items"]) for v in out.values()), "lines")
