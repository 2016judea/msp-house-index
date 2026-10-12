"""Detail for every home in the comp pool (and every listing): the features an
appraiser adjusts for. Garage stalls, basement and whether it is finished, full
and half baths, stories, roof age, the listing's own words (condition), and the
home's whole sale history (repeat sales measure the local price trend).

    caffeinate -i .venv/bin/python scripts/fetch_comp_details.py   # ~3,200 requests, ~90 min, cached

Same endpoint, client and batching as fetch_details.py, through the cached,
throttled msp/realtor.py client. Cache keys carry no date, so a rerun costs
nothing for homes already fetched. Writes data/comp_details.jsonl, one home per
line, trimmed to the fields scripts/build_valuation.py reads.
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from msp.realtor import gql

BATCH = 40  # half the requests of fetch_details.py; a 40-home reply is ~400 KB
HOME = """property_id status last_sold_price last_sold_date
  description { beds baths baths_full baths_half baths_3qtr sqft lot_sqft year_built type sub_type stories garage text }
  details { category text }
  property_history { date event_name price source_name }"""
KEEP_DETAIL = ("Other Rooms", "Garage and Parking", "Building and Construction", "Interior Features", "Bathrooms",
               "Other Property Info", "Land Info", "Basement")
SKIP_TYPES = {"land", "farm"}


def ids():
    root = Path(__file__).resolve().parent.parent / "data"
    sold, seen = [], set()
    for line in open(root / "sold_raw.jsonl"):
        r = json.loads(line)
        if r["property_id"] not in seen:
            seen.add(r["property_id"])
            sold.append(((r.get("description") or {}).get("sold_date") or "", r["property_id"]))
    sold.sort(reverse=True)  # newest first: the backtest holdout lands in the first hour
    out = [p for _, p in sold]
    for line in open(root / "listings_raw.jsonl"):
        r = json.loads(line)
        if r["property_id"] not in seen and (r.get("description") or {}).get("type") not in SKIP_TYPES:
            seen.add(r["property_id"])
            out.append(r["property_id"])
    return out


def trim(v):
    d = v.get("description") or {}
    det = {}
    for x in v.get("details") or []:
        if x.get("category") in KEEP_DETAIL:
            det.setdefault(x["category"], []).extend(x.get("text") or [])
    return {"property_id": v["property_id"], "status": v.get("status"), "description": d, "details": det,
            "property_history": v.get("property_history") or []}


def main():
    root = Path(__file__).resolve().parent.parent / "data"
    out = root / "comp_details.jsonl"
    have = set()
    if out.exists():  # resume: a home already written is never asked for again
        for line in open(out):
            try:
                have.add(json.loads(line)["property_id"])
            except (json.JSONDecodeError, KeyError):
                pass
    todo = [p for p in ids() if p not in have]
    print(f"{len(have)} already on disk, {len(todo)} to fetch", flush=True)
    n = 0
    with open(out, "a") as f:
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            q = "query Q { " + " ".join(f'h{p}: home(property_id: "{p}") {{ {HOME} }}' for p in chunk) + " }"
            try:
                data = gql(q, "RDC_WEB_DETAILS_PAGE", f"compdetail|{','.join(chunk)}")["data"]
            except Exception as e:  # one bad batch must not stop an overnight run
                print(f"batch {i}: {e}", flush=True)
                continue
            for v in data.values():
                if v:
                    f.write(json.dumps(trim(v)) + "\n")
                    n += 1
            f.flush()
            if (i // BATCH) % 50 == 0:
                print(f"{i + len(chunk)}/{len(todo)} written {n}", flush=True)
    print(f"comp details: {n} of {len(todo)}")


if __name__ == "__main__":
    main()
