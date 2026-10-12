#!/usr/bin/env python3
"""What homes on the same block sold for: the "this street" check beside the comps.

    python scripts/build_block.py   # after build_index.py; adds block_* fields to private/data.json

The block is the listing's own street, same city, same hundred (3600-3699 Van Buren St NE),
from the county parcel file (data/county.duckdb). Counted are dwellings other than this
one whose last recorded sale is 2022 or later and over $30k: a $1 family transfer is not
a price. Reported as a median, with the count, so 2 sales read differently from 12.
"""
import json
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parent.parent
con = duckdb.connect()
con.execute(f"attach '{ROOT / 'data/county.duckdb'}' as cty (read_only)")
con.execute(f"create table L as select property_id, county, pin from '{ROOT / 'data/index.parquet'}' where rank_ok and pin is not null")
rows = con.execute("""
with me as (
  select L.property_id, p.county, p.street, p.city, try_cast(regexp_extract(p.anumber, '^\\d+') as int) // 100 as hund, p.pin
  from L join cty.parcels p on p.county = L.county and p.pin = L.pin
  where p.street is not null and p.anumber is not null
)
select me.property_id, median(o.sale_value) as block_med, count(*) as block_n,
       max(o.sale_date) as block_last, any_value(me.street) as street, any_value(me.hund) as hund
from me join cty.parcels o on o.county = me.county and o.street = me.street and o.city = me.city
 and try_cast(regexp_extract(o.anumber, '^\\d+') as int) // 100 = me.hund and o.pin <> me.pin
 and o.is_dwelling and o.sale_date >= date '2022-01-01' and o.sale_value > 30000
group by 1
""").fetchall()
DIRS = {"Ne": "NE", "Nw": "NW", "Se": "SE", "Sw": "SW", "N": "N", "S": "S", "E": "E", "W": "W"}
name = lambda st: " ".join(DIRS.get(w, w) for w in st.title().split())
blk = {r[0]: {"block_med": round(r[1]), "block_n": r[2], "block_name": (f"{r[5] * 100}s " if r[5] else "1–99 ") + name(r[4])} for r in rows}
p = ROOT / "private/data.json"
d = json.loads(p.read_text())
for r in d["rows"]:
    r.update(blk.get(r["property_id"], {}))
p.write_text(json.dumps(d, separators=(",", ":")))
have = [r for r in d["rows"] if "block_med" in r]
print(f"{len(have)} of {len(d['rows'])} listings have block sales; {sum(r['block_n'] >= 3 for r in have)} with 3+")
