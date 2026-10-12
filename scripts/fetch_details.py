"""Per-listing detail: price history, tax/assessment history, AVMs, description.

Batches BATCH homes into one request with GraphQL aliases, so ~14k listings is
~700 requests at REALTOR_DELAY spacing. Cached per batch under cache/realtor/.
Skips listings that cannot be ranked anyway (land, lots) to spare requests.

    python scripts/fetch_details.py     # reads data/listings_raw.jsonl, writes data/details.jsonl
"""
import json, sys, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from msp.realtor import gql

BATCH = 20
HOME = """property_id status list_price
  flags { is_pending is_contingent is_foreclosure is_new_construction }
  description { beds baths sqft lot_sqft year_built type sub_type stories garage text }
  tax_record { apn }
  estimates { current_values { estimate estimate_high estimate_low date source { name } } }
  property_history { date event_name price source_name }
  tax_history { year tax assessment { total land building } }"""
SKIP_TYPES = {"land", "farm"}


def main():
    root = Path(__file__).resolve().parent.parent / "data"
    seen, ids = set(), []
    for line in open(root / "listings_raw.jsonl"):
        r = json.loads(line)
        pid = r["property_id"]
        if pid in seen or (r.get("description") or {}).get("type") in SKIP_TYPES:
            continue
        seen.add(pid)
        ids.append(pid)
    ids.sort()
    today = dt.date.today().isoformat()
    n = 0
    with open(root / "details.jsonl", "w") as f:
        for i in range(0, len(ids), BATCH):
            chunk = ids[i:i + BATCH]
            q = "query Q { " + " ".join(f'h{p}: home(property_id: "{p}") {{ {HOME} }}' for p in chunk) + " }"
            data = gql(q, "RDC_WEB_DETAILS_PAGE", f"detail|{today}|{','.join(chunk)}")["data"]
            for k, v in data.items():
                if v:
                    f.write(json.dumps(v) + "\n")
                    n += 1
            if (i // BATCH) % 25 == 0:
                print(f"{i + len(chunk)}/{len(ids)}", flush=True)
    print(f"details: {n} of {len(ids)}")


if __name__ == "__main__":
    main()
