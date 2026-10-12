"""Enumerate every for-sale listing in the 7-county metro, plus 12 months of
closed sales (the comp pool), from Realtor.com's search GraphQL.

    python scripts/fetch_search.py            # writes data/listings_raw.jsonl, data/sold_raw.jsonl
Pages are cached under cache/realtor/ keyed by county+status+window+offset+day.
"""
import json, sys, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from msp.realtor import gql

COUNTIES = ["Hennepin", "Ramsey", "Anoka", "Dakota", "Washington", "Scott", "Carver"]
TODAY = dt.date.today().isoformat()
PAGE = 200
FIELDS = """property_id listing_id status href list_price list_date last_sold_price last_sold_date
  price_reduced_amount photo_count primary_photo { href } hoa { fee }
  flags { is_pending is_contingent is_foreclosure is_new_construction is_coming_soon is_short_sale }
  description { beds baths sqft lot_sqft year_built type sub_type sold_price sold_date }
  location { address { line city state_code postal_code coordinate { lat lon } } county { name fips_code } }
  advertisers { type name office { name } }"""


def pages(county, status, extra=""):
    out, offset = [], 0
    while True:
        q = (f'query Q {{ home_search(query: {{status: [{status}], {extra} search_location: {{location: "{county} County, MN"}}}}, '
             f'limit: {PAGE}, offset: {offset}, sort: [{{field: list_date, direction: desc}}]) {{ total count results {{ {FIELDS} }} }} }}')
        b = gql(q, "RDC_WEB_SRP_FS_PAGE", f"search|{county}|{status}|{extra}|{offset}|{TODAY}")["data"]["home_search"]
        out += b["results"]
        offset += PAGE
        if offset >= b["total"] or not b["results"]:
            return out, b["total"]


def main():
    root = Path(__file__).resolve().parent.parent / "data"
    root.mkdir(exist_ok=True)
    with open(root / "listings_raw.jsonl", "w") as f:
        for c in COUNTIES:
            rows, total = pages(c, "for_sale")
            for r in rows:
                r["_county_searched"] = c
                f.write(json.dumps(r) + "\n")
            print(f"for_sale {c}: {len(rows)} of {total}", flush=True)
    # Closed sales, one month at a time so no window passes the offset cap.
    start = dt.date.today().replace(day=1) - dt.timedelta(days=365)
    with open(root / "sold_raw.jsonl", "w") as f:
        for c in COUNTIES:
            n = 0
            d = start.replace(day=1)
            while d <= dt.date.today():
                nxt = (d + dt.timedelta(days=32)).replace(day=1)
                win = f'sold_date: {{min: "{d.isoformat()}", max: "{(nxt - dt.timedelta(days=1)).isoformat()}"}},'
                rows, total = pages(c, "sold", win)
                for r in rows:
                    r["_county_searched"] = c
                    f.write(json.dumps(r) + "\n")
                n += len(rows)
                d = nxt
            print(f"sold {c}: {n}", flush=True)


if __name__ == "__main__":
    main()
