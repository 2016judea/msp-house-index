#!/usr/bin/env python3
"""What is around each ranked house: zoning, historic status, cleanup sites, the lead
line, money going into the area, who owns the block, transit and the busiest road.

    python scripts/build_neighborhood.py   # after build_index.py; writes private/nb/<property_id>.json

Every line is a fact about the parcel or a count around it, with its source and date.
Counts around the house are compared with the same count around a sample of homes in
the same city, so "38 permits" reads against what is normal there.

Reads bricks closed_system.duckdb and data/county.duckdb READ-ONLY. All layers used are
`open-public-record` in bricks `layer.licence` (checked at run time; the build stops if
one is not). Owner names and mailing addresses are read only to compare against the
site address and are never written.

Left out on purpose: crime, shots fired, 911, police stops, census demographics
(fair-housing steering; see DATA_MODEL.md, Neighborhood).
"""
import datetime as dt, json, re, shutil
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parent.parent
BRICKS = Path.home() / "Desktop/bricks/data"
GIS = BRICKS / "cache/gis"
OUT = ROOT / "private/nb"

LAYERS = ["zoning_district", "mpls_hpc_landmarks", "stp_historic_sites", "mpca_sites", "mpls_lead_service_lines",
          "mpls_permits", "stp_permits", "stp_new_dwellings___census___approved_building_permits",
          "stp_permit_tppe_demolition_permit", "metro_transit_stops", "mndot_traffic_counts",
          "taxpayer_mail_hennepin", "taxpayer_mail_ramsey", "taxpayer_mail_anoka", "taxpayer_mail_washington",
          "taxpayer_mail_scott", "taxpayer_mail_carver"]

con = duckdb.connect()
con.execute("load spatial")
con.execute(f"attach '{BRICKS / 'closed_system.duckdb'}' as cs (read_only)")
con.execute(f"attach '{ROOT / 'data/county.duckdb'}' as cty (read_only)")

lic = dict(con.execute("select layer, licence from cs.layer").fetchall())
bad = [l for l in LAYERS if lic.get(l) != "open-public-record"]
assert not bad, f"not open-public-record, may not ship: {bad}"
asof = {l: str(p) for l, p in con.execute("select layer, pulled_max from cs.layer").fetchall()}

def q(sql, *a):
    cur = con.execute(sql, *a)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]

# Metres on a local flat projection: plenty at 400 m, 45°N.
MX, MY = 78710.0, 111130.0
con.execute(f"create macro dist(a1, b1, a2, b2) as sqrt(pow((a1 - a2) * {MX}, 2) + pow((b1 - b2) * {MY}, 2))")

# The listing point is its parcel's centroid where we matched one, else Realtor's geocode.
con.execute(f"""
create table L as
select i.property_id, i.county, i.pin, i.city, coalesce(p.lon, i.lon) lon, coalesce(p.lat, i.lat) lat,
       p.street, p.anumber, p.city pcity
from '{ROOT / 'data/index.parquet'}' i
left join cty.parcels p on p.county = i.county and p.pin = i.pin
where i.rank_ok
qualify row_number() over (partition by i.property_id order by p.unit nulls last) = 1
""")

# Points of interest, one table, so every radius count goes through one grid join.
con.execute(f"""
create table P as
with mp as (
  select r.record_id id, r.event_at t, r.attrs a, coalesce(r.lon, pc.lon) lon, coalesce(r.lat, pc.lat) lat, r.layer
  from cs.record r left join cs.parcel pc on pc.parcel_key = r.parcel_key
  where r.layer in ('mpls_permits', 'stp_permits', 'stp_new_dwellings___census___approved_building_permits',
                    'stp_permit_tppe_demolition_permit')
)
select 'mpls_build' k, id, t, lon, lat, null::double v, (a->>'workType') sub from mp
  where layer = 'mpls_permits' and (a->>'permitType') in ('Res', 'Commercial') and t > timestamp '2024-09-05'
union all select 'mpls_demo', id, t, lon, lat, null, (a->>'workType') from mp
  where layer = 'mpls_permits' and (a->>'permitType') = 'Wrecking' and t > timestamp '2024-09-05'
union all select 'mpls_newdw', id, t, lon, lat, (a->>'dwellingUnitsNew')::double, null from mp
  where layer = 'mpls_permits' and try_cast(a->>'dwellingUnitsNew' as int) > 0 and t > timestamp '2024-09-05'
union all select 'stp_build', id, t, lon, lat, try_cast(a->>'EST_VALUE_OF_WORK' as double), (a->>'WORK_TYPE') from mp
  where layer = 'stp_permits' and (a->>'FOLDER_TYPE') = 'Building Permit' and t > timestamp '2023-06-30'
union all select 'stp_demo', id, t, lon, lat, null, (a->>'WORK_TYPE') from mp
  where layer = 'stp_permit_tppe_demolition_permit' and t > timestamp '2023-06-30'
union all select 'stp_newdw', id, t, lon, lat, null, (a->>'SUB_TYPE') from mp
  where layer = 'stp_new_dwellings___census___approved_building_permits' and t > timestamp '2023-06-30'
union all select 'stop', record_id, event_at, lon, lat, null, (attrs->>'stop_name') from cs.record
  where layer = 'metro_transit_stops' and event_at = (select max(event_at) from cs.record where layer = 'metro_transit_stops')
    and coalesce(attrs->>'location_type', '0') in ('0', '')
union all select 'road', record_id, event_at, lon, lat, (attrs->>'CURRENT_VOLUME')::double,
    coalesce(nullif(attrs->>'STREET_NAME', ''), (attrs->>'ROUTE_LABEL')) || ' · ' || coalesce(attrs->>'LOCATION_DESCRIPTION', '') || ' · ' || (attrs->>'CURRENT_YEAR')
  from cs.record where layer = 'mndot_traffic_counts' and (attrs->>'_kind') = 'mndot_aadt'
union all select 'mpca', (attrs->>'site_id'), null, lon, lat, null, attrs::varchar from cs.record
  where layer = 'mpca_sites' and regexp_matches(attrs->>'activity_list',
    'Leak Site|Brownfields|Superfund|CERCLIS|RCRA Remediation|Site Assessment|Integrated Remediation|Contaminated Soil')
""")
con.execute("delete from P where lon is null or lat is null")
# Traffic: one row per count location, its latest year.
con.execute("delete from P where k = 'road' and (id, t) not in (select id, max(t) from P where k = 'road' group by id)")
con.execute(f"alter table P add column cx int; alter table P add column cy int")
con.execute(f"update P set cx = floor(lon * {MX} / 400), cy = floor(lat * {MY} / 400)")

# Comparison sample: 2,500 homes per city, same counts, so a listing's number has a city median.
con.execute(f"""
create table S as
select 'S' || county || pin property_id, case when county = 'hennepin' then 'Minneapolis' else 'Saint Paul' end city, lon, lat
from cty.parcels
where is_dwelling and lon is not null and ((county = 'hennepin' and city = 'MINNEAPOLIS') or (county = 'ramsey' and city = 'ST PAUL'))
qualify row_number() over (partition by county order by hash(pin)) <= 2500
""")

def near(points, radius):
    """(property_id, P row, metres) for every P within radius of each point."""
    return f"""
    with o as (select unnest([-1, 0, 1]) d),
    pt as (select x.property_id, x.lon, x.lat, floor(x.lon * {MX} / 400)::int + o1.d cx, floor(x.lat * {MY} / 400)::int + o2.d cy
           from {points} x, o o1, o o2 where x.lon is not null)
    select pt.property_id, P.*, dist(pt.lon, pt.lat, P.lon, P.lat) m
    from pt join P using (cx, cy) where dist(pt.lon, pt.lat, P.lon, P.lat) <= {radius}"""

con.execute(f"create table N400 as {near('(select property_id, lon, lat from L union all select property_id, lon, lat from S)', 400)} and P.k not in ('mpca')")
con.execute(f"create table N200 as {near('L', 200)} and P.k = 'mpca'")

# ---- zoning: the city's own district polygons (Minneapolis land-use districts, Saint Paul zoning)
con.execute(f"""
create table Z as
select property_id, 'Minneapolis' zcity, z.Land_Use_Code code, z.Land_Use zname from L,
  st_read('{GIS / 'zoning_minneapolis/page_0000000.geojson'}') z where st_contains(z.geom, st_transform(st_point(L.lon, L.lat), 'EPSG:4326', 'EPSG:26915', true))
union all
select property_id, 'Saint Paul', z.Zoning, z.Zoning_Name from L,
  st_read('{GIS / 'zoning_saint_paul/page_0000000.geojson'}') z where st_contains(z.geom, st_transform(st_point(L.lon, L.lat), 'EPSG:4326', 'EPSG:26915', true))
""")
zoning = {r["property_id"]: r for r in q("select * from Z")}

ZDATE = {c: dt.date.fromtimestamp((GIS / d / "page_0000000.geojson").stat().st_mtime).isoformat()
         for c, d in (("Minneapolis", "zoning_minneapolis"), ("Saint Paul", "zoning_saint_paul"))}
MPLS_CODE = ("Minneapolis Code of Ordinances, Title 20 (Zoning Code), effective July 1, 2023",
             "https://minneapolis2040.com/media/1984/minneapolis-zoning-code-july-1-2023.pdf")
STP_CODE = ("Saint Paul Legislative Code ch. 66, as amended by Ord 23-43 (final action 2023-10-18)",
            "https://stpaul.legistar.com/LegislationDetail.aspx?ID=6358356&GUID=F8BB8118-1017-44D6-A1D1-029E7621E410&FullText=1")
# Verbatim purpose sentences from the code (§ cited). Districts not here get code + link only.
PURPOSE = {
 ("Minneapolis", "UN1"): ("§530.110", "The UN1 Urban Neighborhood District allows predominantly small-scale residential uses."),
 ("Minneapolis", "UN2"): ("§530.120", "The UN2 Urban Neighborhood District allows predominantly small to moderate-scale residential uses."),
 ("Minneapolis", "UN3"): ("§530.130", "The UN3 Urban Neighborhood District allows predominantly moderate to large-scale residential uses near transit routes and METRO stations."),
 ("Minneapolis", "RM1"): ("§530.210", "The RM1 Residence Goods and Services District allows predominantly small to moderate-scale residential uses adjacent to goods and services corridors. In addition to uses allowed in the urban neighborhood districts, commercial uses are allowed in mixed-use buildings that include a residential use."),
 ("Minneapolis", "RM2"): ("§530.220", "The RM2 Residence Office and Services District allows residential uses at a range of scales and low impact, small-scale commercial activity."),
 ("Minneapolis", "RM3"): ("§530.230", "The RM3 Residence and Institutional District allows large-scale dwellings, large office uses, and major institutions."),
 ("Minneapolis", "CM1"): ("§530.330", "The CM1 Neighborhood Mixed-Use District allows individual commercial uses and small collections of commercial uses that are typically small in scale and serve a local market."),
 ("Minneapolis", "CM2"): ("§530.340", "The CM2 Corridor Mixed-Use District allows small, moderate, and large-scale commercial uses. Mixed-use multistory development is encouraged."),
 ("Minneapolis", "CM3"): ("§530.350", "The CM3 Community Mixed-Use District allows large-scale mixed-use development with commercial uses fronting on major streets."),
 ("Minneapolis", "CM4"): ("§530.360", "The CM4 Destination Mixed-Use District includes multistory mixed-use development."),
 ("Minneapolis", "DT1"): ("§530.410", "The DT1 Downtown Center District is established to provide an environment for retail and office activities of citywide and regional significance at the center of downtown. The district also allows entertainment, residential, and public uses, which complete the mixed-use core of the city."),
 ("Minneapolis", "DT2"): ("§530.420", "The DT2 Downtown Destination District is established provide a downtown district where commercial uses are required at the street level of all development in this category to encourage pedestrian activity beyond the typical daytime business hours."),
 ("Minneapolis", "PR1"): ("§530.510", "The PR1 Production Mixed-Use includes production, commercial, and warehousing and storage uses. Residential uses are allowed as part of mixed-use buildings that provide production space."),
 ("Minneapolis", "PR2"): ("§530.520", "The PR2 Production and Processing District includes production uses and are designated with the intent of protecting them from encroaching non-production uses. Residential uses are not allowed."),
 ("Saint Paul", "H1"): ("§66.212", "The H1 residential district provides for a variety of housing options along with civic and institutional uses, public services and utilities that serve residents in the district. The district allows for reuse and/or conversion of existing homes and infill development in existing neighborhoods, lots, and backyards, without having to demolish existing viable housing."),
 ("Saint Paul", "H2"): ("§66.213", "The H2 residential district provides for a variety of housing options along with civic and institutional uses, public services and utilities that serve residents in the district. The district allows for reuse and/or conversion of existing homes and infill development in existing neighborhoods, lots, and backyards, without having to demolish existing viable housing. It is intended for use in Neighborhood Nodes and near transit routes along fixed rail and bus rapid transit corridors and high-frequency bus routes."),
}
SHORT = {"UN1": "small-scale homes", "UN2": "small to moderate-scale homes", "UN3": "moderate to large-scale homes near transit",
         "H1": "a variety of housing options, incl. conversions and infill", "H2": "a variety of housing options, incl. conversions and infill, near transit"}

# ---- historic: on the parcel, or a designated site within 40 m of its centroid
hist = q("""
select L.property_id, r.layer, r.attrs, dist(L.lon, L.lat, coalesce(r.lon, pc.lon), coalesce(r.lat, pc.lat)) m,
       r.parcel_key = L.county || ':' || L.pin same
from cs.record r left join cs.parcel pc on pc.parcel_key = r.parcel_key, L
where r.layer in ('mpls_hpc_landmarks', 'stp_historic_sites')
  and (r.parcel_key = L.county || ':' || L.pin or dist(L.lon, L.lat, coalesce(r.lon, pc.lon), coalesce(r.lat, pc.lat)) <= 40)
""")

hist_by = {}
for r in hist:
    hist_by.setdefault(r["property_id"], []).append(r)

# ---- lead service line, Minneapolis: the city's classification of the line(s) on this parcel
LEAD = {1: "Lead", 2: "Non-Lead", 3: "Unknown", 4: "Galvanized Requiring Replacement (GRR)"}  # city's coded-value domain
lead = {}
for r in q("""select L.property_id, list(distinct try_cast((r.attrs->>'Classification') as int)) cls, count(*) n
              from cs.record r join L on r.parcel_key = L.county || ':' || L.pin
              where r.layer = 'mpls_lead_service_lines' group by 1"""):
    lead[r["property_id"]] = r

# ---- who owns the block: same street, city and hundred as build_block.py, dwellings, not this lot
blk = q("""
with me as (select property_id, county, pin, street, pcity, try_cast(regexp_extract(anumber, '^\\d+') as int) // 100 hund
            from L where street is not null and anumber is not null)
select me.property_id, o.county, o.pin, o.anumber, o.street, o.unit, o.is_condo, o.homestead, t.attrs
from me join cty.parcels o on o.county = me.county and o.street = me.street and o.city = me.pcity
  and try_cast(regexp_extract(o.anumber, '^\\d+') as int) // 100 = me.hund and o.pin <> me.pin and o.is_dwelling
left join cs.record t on t.layer = 'taxpayer_mail_' || o.county and t.parcel_key = o.county || ':' || o.pin
""")

def mail_lines(county, a):
    """Address lines of the tax bill (never the name)."""
    if a is None:
        return None
    a = json.loads(a)
    if county == "hennepin":
        ls = [a.get(f"TAXPAYER_NM{s}") for s in ("", "_1", "_2", "_3")]
        ls = [re.sub(r"\s+", " ", x or "").strip() for x in ls]
        ls = [x for x in ls if x]
        # name lines come first; the street line is the first that starts with a number or PO BOX
        i = next((k for k, x in enumerate(ls) if re.match(r"^(\d|P ?O BOX)", x)), None)
        return None if i is None else " ".join(ls[i:]).upper()
    ls = [re.sub(r"\s+", " ", a.get(f"TAX_ADD_L{k}") or "").strip() for k in (1, 2, 3, 4)]
    s = " ".join(x for x in ls if x).upper()
    return s or None

def street_core(st):
    ws = [w for w in re.sub(r"[^A-Z0-9 ]", " ", (st or "").upper()).split() if w not in {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}]
    return ws[0] if ws else ""

def is_home(anumber, street, mail):
    """Does the tax bill go to the house itself? House number + the street's first word
    (ordinals and spacing normalised: 125TH = 125, DUCK LAKE = DUCKLAKE)."""
    hn = re.match(r"\d+", anumber or "")
    core = re.sub(r"(\d+)(ST|ND|RD|TH)$", r"\1", street_core(street))
    if not hn or not core:
        return False
    flat = re.sub(r"(\d+)(ST|ND|RD|TH)\b", r"\1", mail).replace(" ", "")
    return bool(re.search(rf"(^|\s){hn.group(0)}\s", mail)) and core[:5] in flat

# Owned from elsewhere = NOT homesteaded (Minnesota homestead = the owner lives there) AND the
# tax bill goes to another address. Both, because each alone misfires: a homesteaded owner's
# bill can go to a mortgage servicer or a PO box; a non-homestead lot can be a builder's spec home
# with the bill at the site. Dakota publishes no mailing address: homestead alone, said on the line.
block = {}
seen = set()
for r in blk:
    key = (r["property_id"], r["county"], r["pin"])
    if key in seen:
        continue
    seen.add(key)
    b = block.setdefault(r["property_id"], {"homes": 0, "known": 0, "away": 0, "mail": r["county"] != "dakota"})
    b["homes"] += 1
    if r["homestead"] not in ("Yes", "No", "Fractional"):
        continue
    if r["county"] == "dakota":
        b["known"] += 1
        b["away"] += r["homestead"] == "No"
        continue
    m = mail_lines(r["county"], r["attrs"])
    if not m:
        continue
    b["known"] += 1
    b["away"] += r["homestead"] == "No" and not is_home(r["anumber"], r["street"], m)

# ---- assemble
def med(xs):
    xs = sorted(xs)
    return None if not xs else (xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2)

agg = {}
for r in q("select property_id, k, count(*) n, sum(v) v, min(m) nearest from N400 group by 1, 2"):
    agg.setdefault(r["property_id"], {})[r["k"]] = r
city_med = {}
sample_city = dict(con.execute("select property_id, city from S").fetchall())
for k in ("mpls_build", "mpls_demo", "mpls_newdw", "stp_build", "stp_demo", "stp_newdw", "stop"):
    for city in ("Minneapolis", "Saint Paul"):
        ns = [agg.get(pid, {}).get(k, {}).get("n", 0) for pid, c in sample_city.items() if c == city]
        vs = [agg.get(pid, {}).get(k, {}).get("v") or 0 for pid, c in sample_city.items() if c == city]
        city_med[(k, city)] = (med(ns), med(vs), len(ns))

roads = {r["property_id"]: r for r in q("""select property_id, arg_max(sub, v) what, max(v) aadt, arg_max(m, v) m
                                           from N400 where k = 'road' and m <= 100 group by 1""")}
stops = {r["property_id"]: r for r in q("""select property_id, count(*) n, min(m) nearest, arg_min(sub, m) stopname
                                           from N400 where k = 'stop' group by 1""")}
# MPCA cleanup sites. Status is the SITE's active_flag from the bulk layer (MPCA's per-activity
# API, which says which activity is closed, sits behind a bot wall after a few dozen calls,
# 2026-10-11), so a line says "MPCA file active", never "contamination open". Tank-only,
# hazardous-waste-generator, stormwater and air permits are not cleanup sites and are dropped.
CLEANUP = "Leak Site|Brownfields|Superfund|CERCLIS|RCRA Remediation|Site Assessment|Integrated Remediation|Contaminated Soil"
mpca = {}
for r in q("select property_id, id, m, sub, lon, lat from N200 order by m"):
    a = json.loads(r["sub"])
    acts = [x for x in (a.get("activity_list") or "").split("; ") if re.search(CLEANUP, x)]
    mpca.setdefault(r["property_id"], []).append({
        "name": a.get("name"), "m": round(r["m"]), "open": a.get("active_flag") == "Y", "acts": acts,
        "url": a.get("site_url"), "lon": round(r["lon"], 6), "lat": round(r["lat"], 6)})

def fmt_m(m):
    return f"{round(m / 10) * 10:.0f} m"

def money(v):
    return f"${v / 1e6:,.1f}M" if v >= 1e6 else f"${v / 1e3:,.0f}k"

cities = {r["property_id"]: r for r in q("select property_id, city, county from L")}
shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)
cover = {}
total_bytes = 0
for pid in sorted(cities):
    lines, layers = [], {}
    def add(key, text, tone, detail, src):
        lines.append({"k": key, "text": text, "tone": tone, "detail": detail, "src": src})
        cover[key] = cover.get(key, 0) + 1

    z = zoning.get(pid)
    if z:
        city = z["zcity"]
        cite, url = MPLS_CODE if city == "Minneapolis" else STP_CODE
        p = PURPOSE.get((city, z["code"]))
        name = f"{z['code']} {z['zname']}".strip() if z["zname"] and z["zname"] != z["code"] else z["code"]
        text = f"Zoned {z['code']}" + (f": {SHORT[z['code']]}" if z["code"] in SHORT else f" ({z['zname']})" if z["zname"] else "")
        detail = [f"{city} {'land-use district' if city == 'Minneapolis' else 'zoning district'}: {name}."]
        if p:
            detail.append(f"The code, {p[0]}: “{p[1]}”")
        else:
            detail.append("What this district allows is set out in the city's zoning code, linked below.")
        add("zoning", text, "sand", detail, {"name": cite, "url": url, "date": ZDATE[city]})

    for r in sorted(hist_by.get(pid, []), key=lambda r: (not r["same"], r["m"] or 0))[:1]:
        a = json.loads(r["attrs"])
        r["same"] = bool(r["same"]) or (r["m"] is not None and r["m"] <= 15)  # a condo unit inside the landmark building
        if r["layer"] == "mpls_hpc_landmarks":
            nm, url = a.get("Name"), a.get("Web")
            where = "this lot or building" if r["same"] else f"{fmt_m(r['m'])} away"
            text = (f"Minneapolis historic landmark{'' if r['same'] else ' next door'}: {nm}")
            detail = [f"{nm}, {a.get('HOUSENUM') or ''} {a.get('STREETNM') or ''} ({where}). Heritage Preservation Commission status: {(a.get('Status') or '').lower()}."]
            extra = [f"{k} {a[f]}" for k, f in (("built", "Construction_Date"), ("architect", "Architect"), ("style", "Arch_Style")) if a.get(f) and a[f] != "Unknown"]
            if extra:
                detail.append("; ".join(extra).capitalize() + ".")
            detail.append("What a designation means for changes to the building is on the city's preservation page, linked below.")
            src = {"name": "Minneapolis HPC Landmarks", "url": url, "date": asof["mpls_hpc_landmarks"]}
        else:
            nm = a.get("Name")
            where = "this lot or building" if r["same"] else f"{fmt_m(r['m'])} away"
            text = f"Saint Paul historic site{'' if r['same'] else ' next door'}: {nm}"
            detail = [f"{nm}, {a.get('Address')} ({where}). Listing: {a.get('Historic_Listing')}."]
            src = {"name": "Saint Paul Historic Sites", "url": "https://information.stpaul.gov/", "date": asof["stp_historic_sites"]}
        detail[0] = detail[0].replace("  ", " ")
        add("historic", text, "amber" if r["same"] else "sand", detail, src)

    ms = mpca.get(pid, [])
    if ms:
        s0 = ms[0]
        text = (f"1 MPCA cleanup site {fmt_m(s0['m'])} away" if len(ms) == 1
                else f"{len(ms)} MPCA cleanup sites within 200 m, nearest {fmt_m(s0['m'])}")
        detail = [f"{s['name']}, {fmt_m(s['m'])}: {'; '.join(s['acts'])}. MPCA file {'active' if s['open'] else 'inactive'}."
                  for s in ms[:6]]
        if any("Leak Site" in x for s in ms for x in s["acts"]):
            detail.append("MPCA: \u201cLeak sites are locations where a release of petroleum products has occurred from a tank system.\u201d")
        detail.append("Whether each cleanup is finished is on the site's MPCA page.")
        add("mpca", text, "amber" if s0["m"] <= 100 else "sand", detail,
            {"name": "MPCA What's In My Neighborhood", "url": s0["url"], "date": asof["mpca_sites"]})
        layers["mpca"] = [[s["lon"], s["lat"], s["name"], s["m"]] for s in ms]

    ld = lead.get(pid)
    if ld:
        cls = [LEAD[c] for c in ld["cls"] if c in LEAD]
        worst = "Lead" if 1 in ld["cls"] else "GRR" if 4 in ld["cls"] else "Unknown" if 3 in ld["cls"] else "Non-Lead"
        text = {"Lead": "Lead water service line (city inventory)", "GRR": "Galvanized water line the city says needs replacing",
                "Unknown": "Water line material unknown (city inventory)", "Non-Lead": "Water service line is not lead"}[worst]
        detail = [f"Minneapolis classifies the service line{'s' if ld['n'] > 1 else ''} on this parcel as: {', '.join(cls)}.",
                  "This is the city's inventory under the federal lead rule, not a water test. 'Unknown' is a gap in the inventory."]
        add("lead", text, "amber" if worst in ("Lead", "GRR") else "sand", detail,
            {"name": "Minneapolis Service Line Inventory", "url": "https://www.arcgis.com/home/item.html?id=5a1ad12f780e48088160bfa6a18b5a75",
             "date": asof["mpls_lead_service_lines"]})

    zc = (zoning.get(pid) or {}).get("zcity")
    A = agg.get(pid, {})
    if zc:
        pre = "mpls" if zc == "Minneapolis" else "stp"
        win = "Sep 2024 – Sep 2026" if pre == "mpls" else "Jul 2023 – Jun 2025 (Saint Paul's register stops 2025-06-30)"
        b = A.get(f"{pre}_build", {})
        n, v = b.get("n", 0), b.get("v") or 0
        mn, mv, ns = city_med[(f"{pre}_build", zc)]
        text = (f"{n} building permits nearby in 2 years (city median {mn:.0f})" if pre == "mpls"
                else f"{n} building permits nearby, Jul 2023–Jun 2025 (city median {mn:.0f})")
        detail = [f"Building permits issued within 400 m, {win}: {n}." + (f" Declared value {money(v)} (city median {money(mv)})." if pre == "stp" else ""),
                  f"City median is the same count around {ns:,} {zc} homes. Minneapolis permits carry no declared value." if pre == "mpls"
                  else f"City median is the same count around {ns:,} {zc} homes."]
        add("permits", text, "sand", detail, {"name": "Minneapolis CCS Permits" if pre == "mpls" else "Saint Paul building permits",
            "url": "https://opendata.minneapolismn.gov/" if pre == "mpls" else "https://information.stpaul.gov/",
            "date": asof["mpls_permits" if pre == "mpls" else "stp_permits"]})
        nd = A.get(f"{pre}_newdw", {})
        nn = nd.get("n", 0)
        mn2 = city_med[(f"{pre}_newdw", zc)][0]
        nu = round(nd.get("v") or 0)
        units = f" ({nu} homes)" if pre == "mpls" and nn and nu != nn else ""
        if nn:
          add("newdw", f"{nn} new-home permit{'s' if nn != 1 else ''} nearby{units}{' in 2 years' if pre == 'mpls' else ', Jul 2023–Jun 2025'} ({'city median: none' if not mn2 else f'city median {mn2:.0f}'})", "sand",
            [f"Permits for new dwellings within 400 m, {win}; nearest {fmt_m(nd['nearest'])}.", f"City median is the same count around {ns:,} {zc} homes."],
            {"name": "Minneapolis CCS Permits (new dwelling units)" if pre == "mpls" else "Saint Paul New Dwellings (approved building permits)",
             "url": "https://opendata.minneapolismn.gov/" if pre == "mpls" else "https://information.stpaul.gov/",
             "date": asof["mpls_permits" if pre == "mpls" else "stp_new_dwellings___census___approved_building_permits"]})
        dm = A.get(f"{pre}_demo", {})
        dn = dm.get("n", 0)
        mn3 = city_med[(f"{pre}_demo", zc)][0]
        if dn:
          add("demo", f"{dn} demolition{'s' if dn != 1 else ''} nearby{' in 2 years' if pre == 'mpls' else ', Jul 2023–Jun 2025'} ({'city median: none' if not mn3 else f'city median {mn3:.0f}'})",
            "amber" if dn >= max(3, 2 * mn3) else "sand",
            [f"{'Wrecking' if pre == 'mpls' else 'Demolition'} permits within 400 m, {win}; nearest {fmt_m(dm['nearest'])}.", f"City median is the same count around {ns:,} {zc} homes."],
            {"name": "Minneapolis CCS Permits (wrecking)" if pre == "mpls" else "Saint Paul demolition permits",
             "url": "https://opendata.minneapolismn.gov/" if pre == "mpls" else "https://information.stpaul.gov/",
             "date": asof["mpls_permits" if pre == "mpls" else "stp_permit_tppe_demolition_permit"]})

    b = block.get(pid)
    if b and b["known"] >= 3:
        text = f"{b['away']} of {b['known']} homes on this block are owned from elsewhere"
        rule = ("not homesteaded (the owner doesn't live there) and the tax bill goes to another address" if b["mail"]
                else "not homesteaded (the owner doesn't live there); Dakota County publishes no mailing addresses")
        detail = [f"Homes on the same street and hundred that are {rule}: {b['away']} of {b['known']}.",
                  "Usually a landlord or an investor. This listing is not counted."]
        if b["homes"] > b["known"]:
            detail.append(f"{b['homes'] - b['known']} more on the block have no mailing address in the county file.")
        add("block", text, "amber" if b["away"] * 2 > b["known"] else "sand", detail,
            {"name": f"{cities[pid]['county'].title()} County homestead status" + (" and taxpayer mailing addresses (counted, never shown)" if b["mail"] else ""),
             "url": None, "date": asof.get(f"taxpayer_mail_{cities[pid]['county']}", asof["taxpayer_mail_hennepin"])})

    s = stops.get(pid)
    if s:
        add("transit", f"Bus or train stop {fmt_m(s['nearest'])}", "sand",
            [f"Nearest: {s['stopname']}, {fmt_m(s['nearest'])}.", f"{s['n']} Metro Transit stops within 400 m."],
            {"name": "Metro Transit GTFS schedule feed of 2026-08-29", "url": "https://www.metrotransit.org/", "date": "2026-08-29"})
    rd = roads.get(pid)
    if rd:
        add("road", f"{rd['aadt']:,.0f} cars a day on the road {fmt_m(rd['m'])} away",
            "amber" if rd["aadt"] >= 10000 else "sand",
            [f"MnDOT count point {fmt_m(rd['m'])} away: {rd['what']}. Average annual daily traffic {rd['aadt']:,.0f}.",
             "Distance is to the count point, not the road; a house on a counted road can be further from its count point."],
            {"name": "MnDOT traffic counts (AADT)", "url": "https://www.dot.state.mn.us/traffic/data/", "date": asof["mndot_traffic_counts"]})

    if lines:
        doc = {"lines": lines}
        if layers:
            doc["layers"] = layers
        s = json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
        (OUT / f"{pid}.json").write_text(s)
        total_bytes += len(s.encode())

n = len(cities)
print(f"{n} ranked listings; {len(list(OUT.glob('*.json')))} files, {total_bytes / 1e6:.2f} MB, {total_bytes / max(1, len(list(OUT.glob('*.json')))):.0f} B avg")
for k, v in sorted(cover.items(), key=lambda x: -x[1]):
    print(f"  {k:9s} {v:5d}  ({v / n:.0%})")
for (k, c), (mn, mv, ns) in sorted(city_med.items()):
    print(f"  median {k:10s} {c:12s} n={mn} v={mv:.0f} over {ns}")
