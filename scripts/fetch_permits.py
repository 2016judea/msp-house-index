"""Minneapolis building permits (Res + Wrecking) issued since 2024, live from the
city's CCS_Permits ArcGIS layer. APN = Hennepin PID. See the mpls-permits skill.
Saint Paul's register stopped publishing 2025-06-30, so no fresh Saint Paul permits.

    python scripts/fetch_permits.py     # writes data/mpls_permits.jsonl
"""
import json, time
from pathlib import Path
import requests

URL = "https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/CCS_Permits/FeatureServer/0/query"
FIELDS = "APN,Display,permitNumber,permitType,occupancyType,workType,status,milestone,comments,issueDate,completeDate"


def main():
    out = Path(__file__).resolve().parent.parent / "data" / "mpls_permits.jsonl"
    n, offset = 0, 0
    with open(out, "w") as f:
        while True:
            r = requests.get(URL, params={"where": "issueDate >= date '2024-01-01' AND permitType IN ('Res','Wrecking')",
                                          "outFields": FIELDS, "resultOffset": offset, "resultRecordCount": 2000,
                                          "orderByFields": "OBJECTID", "f": "json"}, timeout=120).json()
            feats = r.get("features", [])
            for ft in feats:
                f.write(json.dumps(ft["attributes"]) + "\n")
            n += len(feats)
            offset += len(feats)
            if not r.get("exceededTransferLimit") or not feats:
                break
            time.sleep(0.5)
    print("mpls permits", n)


if __name__ == "__main__":
    main()
