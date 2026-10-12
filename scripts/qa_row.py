"""Print everything behind one or more ranked rows, for QA by hand.
    python scripts/qa_row.py 1 2 3        # by rank
"""
import json, sys
from pathlib import Path
import duckdb
D = Path(__file__).resolve().parent.parent / "data"
det = {}
for line in open(D / "details.jsonl"):
    try:
        r = json.loads(line); det[r["property_id"]] = r
    except json.JSONDecodeError:
        pass
con = duckdb.connect()
for rk in sys.argv[1:]:
    cur = con.execute(f"select * from '{D/'index.parquet'}' where rank = ?", [int(rk)])
    cols = [c[0] for c in cur.description]; r = dict(zip(cols, cur.fetchone()))
    d = det.get(r["property_id"], {})
    print(f"#{rk} {r['address']}, {r['city']} | {r['type_group']} {r['sub_type']} | ask {r['ask']} | sqft {r['sqft']} lot {r['lot_sqft']} parcel {r['parcel_sqft'] and round(r['parcel_sqft'])} | yb {r['year_built']}")
    print(f"   pin {r['pin']} via {r['match_how']} exact={r['emv_exact']} cands={r['n_cand']} site_units={r['site_units']} | EMV {r['emv']} land {r['emv_land']} bldg {r['emv_bldg']} | rdc assessed {r['rdc_assessed']} ({r['rdc_assessed_year']}) | {r['useclass']}")
    print("   ests", r["est_cal"], r["est_comp"], r["est_deed"], "score", r["score"], r["url"])
    print("   hist", [(e['date'], e['event_name'], e['price']) for e in (d.get('property_history') or [])[:6]])
    print("   text", ((d.get('description') or {}).get('text') or '')[:500].replace("\n", " "))
    print("   signals", r["signals"])
    print()
