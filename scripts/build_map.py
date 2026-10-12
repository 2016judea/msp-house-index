#!/usr/bin/env python3
"""The block map: every lot within ~150 m of each ranked listing, with what the county appraises it at.

    python scripts/build_map.py   # after build_index.py; writes site/map/<cell>.json, adds `m` to site/data.json

Lots come from data/county.duckdb (MetroGIS parcels, all 7 counties, UTM 15N metres). They are
cut into 400 m cells; a listing loads the cells its 150 m box touches, so neighbouring listings
share files and an opened row fetches 1-4 small files (lazily, only when opened).

Finished square feet: the county file where it has one; Hennepin publishes none, so Minneapolis
lots take the Minneapolis assessor's 2025 file (bricks parcel_snapshot). Hennepin lots outside
Minneapolis have none in any public file, and the map says so instead of guessing.

Stacked parcels (condo units drawn as the same polygon, or as points inside the building's lot)
fold into one lot with a unit count; its value is the median unit.

Cell file: {"p":[[ring, emv $k, sqft, year, saleYYYYMM, sale$k, "addr", units, "county class if not a home", $/sqft (median unit)], ...],
            "b":[[lon, lat, "name", "category", lot index], ...]}   (businesses: Overture Places, on non-home lots only)
ring = Google polyline encoding (precision 1e5 → ~1 m) of the outer ring, simplified to 1 m (one coordinate = a unit drawn as a point); rings of a multipolygon
are joined with a space. Nulls are 0 / "".
"""
import json, re, shutil
from collections import defaultdict
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site/map"
BRICKS = Path.home() / "Desktop/bricks/data/closed_system.duckdb"
R, CELL = 150, 400  # metres: frame half-width around a listing, cell size

c = duckdb.connect()
c.execute("load spatial")
c.execute(f"attach '{ROOT / 'data/county.duckdb'}' as d (read_only)")
c.execute(f"attach '{BRICKS}' as cs (read_only)")

# Listings, at their own lot's centroid (or the unit point).
c.execute(f"""create table L as select l.property_id, p.county, p.pin,
  ST_X(ST_Centroid(p.geom)) x, ST_Y(ST_Centroid(p.geom)) y
  from '{ROOT / 'data/index.parquet'}' l join d.parcels p on p.county = l.county and p.pin = l.pin
  where l.rank_ok and p.geom is not null qualify row_number() over (partition by l.property_id) = 1""")
c.execute(f"""create table C as select distinct cx, cy from L,
  range(cast(floor((x-{R})/{CELL}) as int), cast(floor((x+{R})/{CELL}) as int)+1) t(cx),
  range(cast(floor((y-{R})/{CELL}) as int), cast(floor((y+{R})/{CELL}) as int)+1) u(cy)""")
c.execute("""create table MSQ as select parcel_key, max(fin_sq_ft) sq from cs.parcel_snapshot
  where vintage = 'minneapolis_assessor_2025' and fin_sq_ft > 300 group by 1""")
# Every parcel whose centroid falls in a needed cell.
c.execute(f"""create table P as select p.county, p.pin, p.geom, p.emv_total emv,
  coalesce(case when p.fin_sq_ft > 300 then p.fin_sq_ft end, m.sq) sq,
  case when p.year_built between 1800 and 2030 then p.year_built end yb,
  p.sale_date, p.sale_value, p.anumber, p.street, p.unit, p.useclass, p.is_dwelling, ST_GeometryType(p.geom)::varchar gt,
  floor(ST_X(ST_Centroid(p.geom))/{CELL})::int cx, floor(ST_Y(ST_Centroid(p.geom))/{CELL})::int cy
  from d.parcels p left join MSQ m on m.parcel_key = p.parcel_key
  join C on C.cx = floor(ST_X(ST_Centroid(p.geom))/{CELL}) and C.cy = floor(ST_Y(ST_Centroid(p.geom))/{CELL})
  where p.geom is not null""")
# Keep only lots inside some listing's frame (+40 m so a lot cut by the frame edge still draws whole).
c.execute(f"""delete from P where not exists (select 1 from L where abs(ST_X(ST_Centroid(P.geom)) - L.x) <= {R + 40}
  and abs(ST_Y(ST_Centroid(P.geom)) - L.y) <= {R + 40})""")
# Lots: polygons grouped by identical shape (stacked condo units), then points folded into the lot holding them.
c.execute("""create table G as select *, dense_rank() over (order by round(ST_X(ST_Centroid(geom)),1), round(ST_Y(ST_Centroid(geom)),1), round(ST_Area(geom))) gid
  from P where gt in ('POLYGON','MULTIPOLYGON')""")
c.execute("""create table PT as select p.*, g.gid from P p join (select gid, any_value(geom) geom from G group by gid) g
  on ST_Contains(g.geom, p.geom) where p.gt = 'POINT'""")
# Units the county drew only as points, with no lot around them (Dakota: 18,882): each spot is its own lot, drawn as a dot.
c.execute("""create table PO as select p.*, 1e9 + dense_rank() over (order by round(ST_X(geom)), round(ST_Y(geom))) gid
  from P p where gt = 'POINT' and not exists (select 1 from PT where PT.county = p.county and PT.pin = p.pin)""")
c.execute("create table U as select * exclude (gid), gid from G union all by name select * from PT union all by name select * from PO")
lots = c.execute(f"""
  with shape as (select gid, any_value(cx) cx, any_value(cy) cy,
      ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(any_value(geom), 1.0), 'EPSG:26915', 'EPSG:4326', always_xy := true)) gj
    from (select gid, cx, cy, geom from G union all select gid, cx, cy, geom from PO) group by gid),
  -- a lot's numbers: the median unit when several parcels share it
  -- the units that count: dwellings when the lot has any (not the $9k parking stalls), else every parcel
  w as (select *, bool_or(coalesce(is_dwelling, false)) over (partition by gid) has_home,
      case when sq > 0 and emv > 0 then emv / sq end ppsf from U),
  v as (select gid, count(*) filter (where is_dwelling or not has_home) n,
      median(emv) filter (where is_dwelling or not has_home) emv, median(sq) filter (where is_dwelling or not has_home) sq,
      median(ppsf) filter (where is_dwelling or not has_home) ppsf, median(yb) yb,
      arg_max(sale_date, sale_date) sd, arg_max(sale_value, sale_date) sv,
      arg_max(anumber, emv) an, arg_max(street, emv) st,
      -- not a home: the county's own class when no parcel on the lot is a dwelling, or it is commercial/industrial/apartment/mixed
      case when not bool_or(coalesce(is_dwelling, false)) then arg_max(coalesce(useclass, 'Not a home'), emv) end cls
    from w group by gid)
  select s.gid, s.cx, s.cy, s.gj, v.n, v.emv, v.sq, v.ppsf, v.yb, v.sd, v.sv, v.an, v.st, v.cls from shape s join v using (gid)""").fetchall()
# Businesses on non-home lots: Overture Places (CDLA-Permissive-2.0), point in lot. Google ratings and
# MN SOS bulk data are NOT used: both are in bricks' RIGHTS_WITHHELD class and a file here is redistribution.
OVT = Path.home() / "Desktop/bricks/data/cache/overture_places_msp.parquet"
biz = c.execute(f"""with o as (select name, category, lon, lat,
    ST_Transform(ST_Point(lon, lat), 'EPSG:4326', 'EPSG:26915', always_xy := true) g
    from '{OVT}' where name is not null and confidence >= 0.5),
  lots as (select gid, any_value(geom) geom from G group by gid
    having not bool_or(coalesce(is_dwelling, false)))
  select lots.gid, o.name, o.category, o.lon, o.lat from o join lots on ST_Contains(lots.geom, o.g)""").fetchall()
own = dict(c.execute("""select L.property_id, U.gid from L join U on U.county = L.county and U.pin = L.pin""").fetchall())


def enc(coords):
    """Google polyline, precision 1e5, lon/lat order."""
    out, px, py = [], 0, 0
    for lon, lat in coords:
        x, y = round(lon * 1e5), round(lat * 1e5)
        for v in (x - px, y - py):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1f)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        px, py = x, y
    return "".join(out)


def rings(gj):
    g = json.loads(gj)
    if g["type"] == "Point":
        return enc([g["coordinates"]])
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"] if g["type"] == "MultiPolygon" else []
    return " ".join(enc(p[0][:-1]) for p in polys if p and len(p[0]) > 3)


DIRS = {"Ne": "NE", "Nw": "NW", "Se": "SE", "Sw": "SW"}
cells = defaultdict(list)
where = {}
for gid, cx, cy, gj, n, emv, sq, ppsf, yb, sd, sv, an, st, cls in lots:
    ring = rings(gj)
    if not ring:
        continue
    street = re.sub(r"(\d)(St|Nd|Rd|Th)\b", lambda m: m[1] + m[2].lower(), " ".join(DIRS.get(w, w) for w in (st or "").title().split()))
    addr = " ".join(x for x in [an or "", street] if x)
    # county class in plain words: "234 3A INDUSTRIAL PREFERENTIAL" -> "Industrial preferential"
    cls = re.sub(r"^(\d+\s+)?\d[a-zA-Z]\s+", "", cls).capitalize() if cls else ""
    sale = (sd.year * 100 + sd.month) if sd else 0
    key = f"{cx}_{cy}"
    where[gid] = (key, len(cells[key]))
    cells[key].append([ring, round((emv or 0) / 1000), int(sq or 0), int(yb or 0), sale,
                       round((sv or 0) / 1000) if sale else 0, addr, int(n or 1), cls or "", round(ppsf or 0)])
bizz = defaultdict(list)
for gid, name, cat, lon, lat in biz:
    if gid in where:
        key, li = where[gid]
        bizz[key].append([round(lon, 6), round(lat, 6), name.strip(), (cat or "").replace("_", " "), li])

if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
total = 0
for key, ps in cells.items():
    b = json.dumps({"p": ps, "b": bizz.get(key, [])}, separators=(",", ":"))
    (OUT / f"{key}.json").write_text(b)
    total += len(b)

# Each ranked row: its frame's cells and its own lot.
lr = {r[0]: r[1:] for r in c.execute("select property_id, x, y from L").fetchall()}
p = ROOT / "site/data.json"
d = json.loads(p.read_text())
n_map = 0
for r in d["rows"]:
    r.pop("m", None)
    if r["property_id"] not in lr:
        continue
    x, y = lr[r["property_id"]]
    ks = [f"{cx}_{cy}" for cx in range(int((x - R) // CELL), int((x + R) // CELL) + 1)
          for cy in range(int((y - R) // CELL), int((y + R) // CELL) + 1) if f"{cx}_{cy}" in cells]
    o = where.get(own.get(r["property_id"]))
    if not ks or not o:
        continue
    r["m"] = [ks, ks.index(o[0]) if o[0] in ks else -1, o[1]]
    n_map += 1
p.write_text(json.dumps(d, separators=(",", ":")))
nlots = sum(len(v) for v in cells.values())
print(f"{len(biz):,} businesses on non-home lots")
print(f"{n_map} of {len(d['rows'])} listings get a map; {nlots:,} lots in {len(cells):,} cells, {total / 1e6:.1f} MB")
