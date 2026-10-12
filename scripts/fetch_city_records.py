#!/usr/bin/env python3
"""Pull the Minneapolis records that carry the specifics, live from the city's ArcGIS:

  CaseViolations   every code violation cited on a parcel: code, plain description, status, dates
  Public_311_YYYY  property complaints (nuisance, housing conditions, dumping...) 2023 on, geocoded

    python scripts/fetch_city_records.py   # writes data/mpls_violations.jsonl, data/mpls_311.jsonl

ArcGIS caps a page at 2,000 rows silently, so every query pages with resultOffset
(bricks memory: reference_arcgis_reads_cap_at_2000).
"""
import json, time
from pathlib import Path
import duckdb, requests

ROOT = Path(__file__).resolve().parent.parent
B = "https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services"
# Complaints about a property, not the street in front of it.
TYPES_311 = ["Exterior Nuisance Complaint", "Exterior Nuisance - Self Service", "Residential Conditions Complaint",
             "Residential Conditions Complaint Tenant", "RHOD Tenant Conditions Complaint", "RHOD Conditions Complaint",
             "Rental License Followup", "Illegal Dumping Complaint", "Zoning Ordinance Complaint", "Property Management Complaint",
             "Licensing Complaint", "Inoperable Vehicle Complaint", "Land Pollution Complaint", "Noise Pollution Complaint",
             "Sewer Issues", "Water Service Issue", "Vacant Building Complaint", "Hazardous Building Complaint"]

def page(url, params):
    out, off = [], 0
    while True:
        r = requests.post(url, data={**params, "resultOffset": off, "resultRecordCount": 2000, "f": "json"}, timeout=120).json()
        if "error" in r:
            raise SystemExit(f"{url}: {r['error']}")
        feats = r.get("features", [])
        out += feats
        if not r.get("exceededTransferLimit") and len(feats) < 2000:
            return out
        off += len(feats)
        time.sleep(0.3)

apns = [r[0] for r in duckdb.sql(f"select distinct pin from '{ROOT / 'data/index.parquet'}' where county = 'hennepin' and pin is not null").fetchall()]
with open(ROOT / "data/mpls_violations.jsonl", "w") as f:
    n = 0
    for i in range(0, len(apns), 200):
        chunk = ",".join(f"'{a}'" for a in apns[i:i + 200])
        for ft in page(f"{B}/CaseViolations/FeatureServer/0/query", {"where": f"APN in ({chunk})", "outFields": "*"}):
            f.write(json.dumps(ft["attributes"]) + "\n"); n += 1
print("violations", n)

with open(ROOT / "data/mpls_311.jsonl", "w") as f:
    n = 0
    for yr in (2023, 2024, 2025, 2026):
        types = ",".join("'" + t.replace("'", "''") + "'" for t in TYPES_311)
        for ft in page(f"{B}/Public_311_{yr}/FeatureServer/0/query", {"where": f"TYPENAME in ({types})", "outFields": "CASEID,TYPENAME,OPENEDDATETIME,CLOSEDDATETIME,CASESTATUS",
                                                                     "returnGeometry": "true", "outSR": "4326"}):
            g = ft.get("geometry") or {}
            if g.get("x"):
                f.write(json.dumps({**ft["attributes"], "lon": g["x"], "lat": g["y"]}) + "\n"); n += 1
print("311", n)
