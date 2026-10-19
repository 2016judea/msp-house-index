"""Build the ranked index: every for-sale listing joined to its county parcel,
valued three independent ways, scored, and explained in one plain line.

    python scripts/build_index.py
Inputs  data/listings_raw.jsonl, data/details.jsonl, data/sold_raw.jsonl,
        data/county.duckdb (scripts/build_county.py), data/mpls_permits.jsonl,
        bricks closed_system.duckdb (St Paul permits, Mpls cases, St Paul vacant
        buildings; read-only) and bricks data/deed_risk_parcels.csv.
Outputs data/index.parquet, data/index.csv, private/data.json + private/evidence.json (the full
set, gitignored); scripts/build_public.py writes the public top 10 into site/

No number here comes from a model. Every figure is a join or arithmetic on one,
and the columns that carry it are in the output beside it.
"""
import json, math, re, statistics, datetime as dt
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
BRICKS = Path.home() / "Desktop/bricks/data"
TODAY = dt.date.today()

# Assessment valuation dates. Minnesota values every parcel as of Jan 2 of the
# assessment year; MetroGIS MKT_YEAR says which year each county's EMV is
# (Hennepin's market_value matched Realtor's 2026 assessment to the dollar).
ASSESSED = {"hennepin": "2026-01-02", "dakota": "2026-01-02", "scott": "2026-01-02",
            "washington": "2026-01-02", "carver": "2026-01-02", "ramsey": "2025-01-02", "anoka": "2025-01-02"}

TYPE_GROUP = {"single_family": "house", "townhomes": "townhome", "condos": "condo", "condo_townhome_rowhome_coop": "condo",
              "multi_family": "multi", "mobile": "mobile", "land": "land", "farm": "land"}

DISTRESS = re.compile(r"\b(as[- ]is|fixer|handyman|investor special|fire damage|needs (?:a lot of )?work|tlc|cash only|cash or rehab|"
                      r"not financeable|rehab loan|unfinished|gutted|shell|uninhabitable|condemned|mold|foundation (?:issues|problems)|code compliance|category [23] vacant|lender[- ]owned|bank[- ]owned|complete rehab\w*|full rehab\w*|major rehab\w*|fire[- ]damaged|freeze damage|water damage|(?:major|significant) repairs)\b", re.I)
TEARDOWN = re.compile(r"\b(tear[- ]?down|teardown|value (?:is )?in the land|buildable lot|build your (?:dream|own) home|lot value|land value)\b", re.I)
MULTIPARCEL = re.compile(r"\b(two|2|three|3|multiple) (?:separate )?(?:parcels|pids|tax ids|lots)\b|\bparcels? (?:included|being sold together)\b|\bplus (?:an? )?(?:adjacent|additional) (?:lot|parcel)\b", re.I)
RESTRICTED = re.compile(r"land trust|\bCLT\b|income[- ](?:restrict|limit|qualif|eligib)|affordab\w+ (?:covenant|program|requirement)|resale restrict|\bAMI\b|area median income", re.I)
SENIOR = re.compile(r"\b(55\+|55 and (?:over|older)|age[- ]restricted|senior (?:living|community|co-?op))", re.I)
COOP = re.compile(r"\b(co-?op|cooperative|share loan|underlying mortgage|shares? of stock)\b", re.I)
# Condition, from the listing's own words. Each phrase here was checked against how it
# reads in real Twin Cities listings (2026-10-11): bare "as is" also matches "enjoy it as
# is" and "as is downtown Hopkins", "gutted" is usually "gutted and remodeled", and
# "shell" is new-construction condo talk, so those only count in the forms below.
COND_TXT = {
    "condemned": re.compile(r"\b(condemned|uninhabitable|not habitable|category 3 vacant)\b", re.I),
    "heavy": re.compile(r"\b((?:complete|full|major|total|extensive) (?:rehab\w*|renovation needed)|gutted to the studs|(?:totally |completely )?gutted(?: inside)?(?![\s\S]{0,40}(?:remodel|rebuil|redone|updated|expanded|cleaned|and |& ))|"
                       r"fire[- ]damage[d]?|freeze damage|water damage|foundation (?:issues|problems)|not financeable|code compliance|category 2 vacant|(?:major|significant) repairs)\b", re.I),
    "light": re.compile(r"(\bas-is\b|\b(?:sold|selling|offered|listed|sale|purchase)\s+(?:in\s+)?[\"'“]?as is\b|\bas is[\"'”]? condition|\btlc\b|\bfixer\b|\bhandyman\b|\binvestor special\b|"
                       r"\bneeds (?:a lot of |some )?work\b|\bcash (?:offers )?only\b|\bcash or rehab\b|\brehab loan\b|\bmold\b|\bprice reflects (?:the )?(?:work|condition|as-is)|will not make any repairs)", re.I),
}
AUCTION = re.compile(r"\b(auction|starting bid|opening bid|bid deadline|highest and best)\b", re.I)


PLURAL = {"house": "houses", "townhome": "townhomes", "condo": "condos", "multi": "multi-family homes"}


CENTRAL = __import__("zoneinfo").ZoneInfo("America/Chicago")


def local_date(stamp):
    """Realtor's list_date is a UTC instant ("2026-10-12T01:30:00.000000Z"); the build's TODAY is the
    Minneapolis date. Slicing [:10] took the UTC date, so an evening listing was dated tomorrow and a
    same-evening build gave it -1 days on market (which SaleListModel bins as no DOM at all)."""
    if not stamp:
        return None
    s = str(stamp)
    if len(s) <= 10:
        return s[:10]
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).astimezone(CENTRAL).date().isoformat()


def money(x):
    if x is None:
        return "—"
    x = float(x)
    return f"${x/1e6:.2f}M" if x >= 1e6 else f"${x/1e3:.0f}k"


def load_listings():
    rows = {}
    for line in open(D / "listings_raw.jsonl"):
        r = json.loads(line)
        rows.setdefault(r["property_id"], r)
    details = {}
    if (D / "details.jsonl").exists():
        for line in open(D / "details.jsonl"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:  # a fetch still writing its last line
                continue
            details[r["property_id"]] = r
    out = []
    for pid, r in rows.items():
        d = r.get("description") or {}
        a = (r.get("location") or {}).get("address") or {}
        co = (a.get("coordinate") or {})
        fl = r.get("flags") or {}
        det = details.get(pid) or {}
        dd = det.get("description") or {}
        hist = sorted(det.get("property_history") or [], key=lambda e: e.get("date") or "", reverse=True)
        # The current listing run = events since the newest "Listed" event.
        run, orig, relisted = [], None, False
        for e in hist:
            run.append(e)
            if (e.get("event_name") or "").lower() in ("listed", "relisted"):
                orig = e.get("price")
                relisted = (e.get("event_name") or "").lower() == "relisted"
                break
        cuts = [e for e in run if "price" in (e.get("event_name") or "").lower()]
        prices = [e.get("price") for e in reversed(run) if e.get("price")]
        n_cuts = sum(1 for p0, p1 in zip(prices, prices[1:]) if p1 < p0)
        mls_sold = next((e for e in hist if (e.get("event_name") or "").lower() == "sold" and e.get("price")), None)
        taxes = sorted([t for t in det.get("tax_history") or [] if (t.get("assessment") or {}).get("total")], key=lambda t: -t["year"])
        est = (det.get("estimates") or {}).get("current_values") or []
        seller = next((x for x in r.get("advertisers") or [] if x.get("type") == "seller"), {}) or {}
        text = dd.get("text") or ""
        list_date = local_date(r.get("list_date"))
        out.append(dict(
            property_id=pid, listing_id=r.get("listing_id"), url=r.get("href"),
            address=a.get("line"), city=a.get("city"), zip=a.get("postal_code"),
            county=((r.get("location") or {}).get("county") or {}).get("name", r["_county_searched"]).lower(),
            lat=co.get("lat"), lon=co.get("lon"),
            type=d.get("type"), type_group=TYPE_GROUP.get(d.get("type"), d.get("type")),
            sub_type=dd.get("sub_type") or d.get("sub_type"),
            beds=d.get("beds"), baths=d.get("baths"), sqft=d.get("sqft"), lot_sqft=d.get("lot_sqft"), year_built=d.get("year_built"),
            ask=r.get("list_price"), list_date=list_date,
            dom=(TODAY - dt.date.fromisoformat(list_date)).days if list_date else None,
            original_ask=orig, n_cuts=n_cuts, relisted=relisted,
            pending=bool(fl.get("is_pending") or fl.get("is_contingent")),
            new_construction=bool(fl.get("is_new_construction")), foreclosure=bool(fl.get("is_foreclosure")),
            short_sale=bool(fl.get("is_short_sale")), hoa=(r.get("hoa") or {}).get("fee"),
            photo=((r.get("primary_photo") or {}).get("href") or "").replace("http:", "https:").replace("s.jpg", "rd-w480_h360.jpg") or None,
            brokerage=(seller.get("office") or {}).get("name"),
            mls_sold_price=(mls_sold or {}).get("price"), mls_sold_date=(mls_sold or {}).get("date"),
            rdc_assessed=taxes[0]["assessment"]["total"] if taxes else None,
            rdc_assessed_year=taxes[0]["year"] if taxes else None,
            rdc_assessed_all=json.dumps(sorted({t["assessment"]["total"] for t in taxes})),
            rdc_avm=round(statistics.mean(e["estimate"] for e in est)) if est else None,
            rdc_avm_n=len(est),
            has_detail=bool(det),
            cond_text={t: sorted({m.group(0).strip().lower() for m in rx.finditer(text)}) for t, rx in COND_TXT.items()},
            distress=", ".join(sorted({m.lower() for m in DISTRESS.findall(text)})) or None,
            teardown_text=bool(TEARDOWN.search(text)), multiparcel_text=bool(MULTIPARCEL.search(text)), auction_text=bool(AUCTION.search(text)),
            restricted_text=bool(RESTRICTED.search(text)), senior_text=bool(SENIOR.search(text)),
            coop_text=bool(COOP.search(text)) or "coop" in (dd.get("sub_type") or "").lower(),
        ))
    return out


def load_sold():
    seen, out = set(), []
    for line in open(D / "sold_raw.jsonl"):
        r = json.loads(line)
        d = r.get("description") or {}
        co = ((r.get("location") or {}).get("address") or {}).get("coordinate") or {}
        price = d.get("sold_price") or r.get("last_sold_price")
        date = d.get("sold_date") or r.get("last_sold_date")
        key = (r["property_id"], date)
        if key in seen or not price or not d.get("sqft") or not co.get("lat"):
            continue
        seen.add(key)
        out.append(dict(property_id=r["property_id"], type_group=TYPE_GROUP.get(d.get("type"), d.get("type")), price=price, sold_date=date,
                        sqft=d["sqft"], year_built=d.get("year_built"), beds=d.get("beds"), lat=co["lat"], lon=co["lon"],
                        address=((r.get("location") or {}).get("address") or {}).get("line")))
    return out


def main():
    con = duckdb.connect()
    con.execute("install spatial; load spatial")
    con.execute(f"attach '{D / 'county.duckdb'}' as cty (read_only)")
    con.execute(f"attach '{BRICKS / 'closed_system.duckdb'}' as cs (read_only)")
    listings = load_listings()
    sold = load_sold()
    con.execute("create table L as select * from read_json_auto(?)", [str(_dump(listings, "listings"))])
    con.execute("create table S as select * from read_json_auto(?)", [str(_dump(sold, "sold"))])
    # A sold row whose $/sqft is outside any Twin Cities home is a data error (a
    # whole building's price on one unit, a sqft typo); left in, one such comp put a
    # Hopkins condo at $3,720/sqft and $4M. Drop them before they become comps.
    con.execute("delete from S where sqft is null or sqft < 300 or price / sqft not between 30 and 1000")
    xy = "ST_Transform(ST_Point(lon, lat), 'EPSG:4326', 'EPSG:26915', always_xy := true)"
    con.execute(f"alter table L add column pt geometry; update L set pt = {xy} where lat is not null")
    con.execute(f"alter table S add column pt geometry; update S set pt = {xy}")
    con.execute("alter table L add column x double; alter table L add column y double; update L set x = ST_X(pt), y = ST_Y(pt)")
    con.execute("alter table S add column x double; alter table S add column y double; update S set x = ST_X(pt), y = ST_Y(pt)")

    # ---- 1. Parcel match ---------------------------------------------------
    # Candidates: parcels the listing point falls in, plus same house number +
    # first street word in the same county. A candidate whose EMV equals one of
    # the assessments Realtor carries for this property is the match (that is how
    # a condo unit is told apart from its 200 siblings in one footprint).
    # Street key: house number + first word of the street name, directionals dropped.
    skey = lambda col: (f"lower(split_part(trim(regexp_replace({col}, '^(N|S|E|W|NE|NW|SE|SW|North|South|East|West)\\s+', '', 'i')), ' ', 1))")
    con.execute(f"""
    create table P as
    select county, pin, geom, emv_total, is_dwelling, anumber::varchar as hn, {skey('street')} as st, unit,
           floor(ST_XMin(geom)/250)::int as cx0, floor(ST_XMax(geom)/250)::int as cx1,
           floor(ST_YMin(geom)/250)::int as cy0, floor(ST_YMax(geom)/250)::int as cy1
    from cty.parcels
    """)
    con.execute("""
    create table PC as select county, pin, unnest(range(cx0, cx1 + 1)) as cx, cy0, cy1 from P where cx1 - cx0 < 40 and cy1 - cy0 < 40
    """)
    con.execute("create table PCC as select county, pin, cx, unnest(range(cy0, cy1 + 1)) as cy from PC")
    con.execute(f"""
    alter table L add column hn varchar; alter table L add column st varchar; alter table L add column unit varchar;
    update L set unit = nullif(ltrim(upper(regexp_extract(address, '(?:\\b(?:Apt|Unit|Ste|Suite|#)\\s*#?\\s*)([A-Za-z0-9-]+)\\s*$', 1)), '0'), ''),
                 hn = regexp_extract(address, '^(\\d+)', 1),
                 st = {skey("regexp_replace(address, '^\\d+\\S*\\s+', '')")}
    """)
    con.execute("""
    create table cand as
    select l.property_id, p.county, p.pin, 'point' as how
    from L l join PCC c on c.county = l.county and c.cx = floor(l.x/250)::int and c.cy = floor(l.y/250)::int
    join P p on p.county = c.county and p.pin = c.pin and ST_Contains(p.geom, l.pt)
    union
    select l.property_id, p.county, p.pin, 'address' from L l
    join P p on p.county = l.county and p.hn = l.hn and p.st = l.st
    union
    select l.property_id, p.county, p.pin, 'unit' from L l
    join P p on p.county = l.county and p.hn = l.hn and p.st = l.st and p.unit = l.unit
    where l.unit is not null
    """)
    con.execute("""
    create table m as
    with c as (
      select c.property_id, c.pin, c.county, string_agg(distinct c.how, '+') as how, any_value(p.emv_total) as emv_total,
             any_value(p.is_dwelling) as is_dwelling,
             any_value(p.emv_total) = any_value(l.rdc_assessed) as emv_exact,
             bool_or(c.how = 'unit') as unit_match
      from cand c join P p using (county, pin) join L l using (property_id)
      group by c.property_id, c.pin, c.county
    )
    select *, count(*) over (partition by property_id) as n_cand from c
    qualify row_number() over (partition by property_id order by unit_match desc, emv_exact desc nulls last, (how like '%point%') desc, is_dwelling desc, emv_total desc) = 1
    """)
    con.execute("""
    create table J as
    select l.*, m.pin, m.how as match_how, m.emv_exact, m.unit_match, m.n_cand,
           p.emv_total as emv, p.emv_land, p.emv_bldg, p.area_sqft as parcel_sqft, p.useclass, p.is_condo as county_condo,
           p.num_units, p.homestead, p.sale_date as deed_date, p.sale_value as deed_price, p.year_built as county_year_built,
           ps.units as site_units
    from L l left join m using (property_id)
    left join cty.parcels p on p.county = m.county and p.pin = m.pin
    left join (select county, pin, count(*) over (partition by county, round(ST_X(ST_Centroid(geom))), round(ST_Y(ST_Centroid(geom)))) as units
                 from P where (county, pin) in (select county, pin from m)) ps
      on ps.county = m.county and ps.pin = m.pin
    """)

    # ---- 2. County-calibrated value -----------------------------------------
    # Nearest 30 arm's-length recorded sales (2025+) whose EMV is within ±35%
    # of this parcel's, inside 8 km. EMV × their median sale/EMV ratio.
    con.execute("""
    create table cal as
    with s as (select *, ST_X(ST_Centroid(geom)) sx, ST_Y(ST_Centroid(geom)) sy from cty.sales),
    sg as (select *, floor(sx/8000)::int + dx as gx, floor(sy/8000)::int + dy as gy from s,
           (select unnest([-1,0,1]) dx), (select unnest([-1,0,1]) dy)),
    pairs as (
      select j.property_id, s.ratio, sqrt((j.x - s.sx)^2 + (j.y - s.sy)^2) as dist
      from J j join sg s on s.gx = floor(j.x/8000)::int and s.gy = floor(j.y/8000)::int
       and s.emv between j.emv / 1.35 and j.emv * 1.35
      where j.emv > 0 and not (s.county = j.county and s.pin = j.pin)
    ), near as (select * from pairs qualify row_number() over (partition by property_id order by dist) <= 30)
    select property_id, median(ratio) as cal_ratio, count(*) as cal_n, max(dist) as cal_radius_m from near group by 1
    """)

    # ---- 3. Comparable-sales value ------------------------------------------
    # Closed MLS sales in the last 12 months: same kind of home, finished sqft
    # within ±25%, built within 25 years (when both known), nearest 10 inside
    # 1.6 km, else inside 3.2 km. Median $/sqft × this home's sqft.
    con.execute("""
    create table comp as
    with sg as (select *, floor(x/3200)::int + dx as gx, floor(y/3200)::int + dy as gy from S,
           (select unnest([-1,0,1]) dx), (select unnest([-1,0,1]) dy)),
    pairs as (
      select j.property_id, s.price / s.sqft as ppsf, s.address as comp_addr, s.price as comp_price, s.sold_date,
             sqrt((j.x - s.x)^2 + (j.y - s.y)^2) as dist
      from J j join sg s on s.type_group = j.type_group and s.gx = floor(j.x/3200)::int and s.gy = floor(j.y/3200)::int
       and s.sqft between j.sqft * 0.75 and j.sqft * 1.25
       and (j.year_built is null or s.year_built is null or abs(j.year_built - s.year_built) <= 25)
       and s.property_id <> j.property_id
      where j.sqft > 0
    ), near as (
      select * from pairs where dist <= 3200 qualify row_number() over (partition by property_id order by dist) <= 10
    )
    select property_id, median(ppsf) as comp_ppsf, count(*) as comp_n, max(dist) as comp_radius_m,
           arg_min(comp_addr, dist) as comp_nearest_addr, arg_min(comp_price, dist) as comp_nearest_price, false as same_building
    from near group by 1 having count(*) >= 4
    """)
    # A condo's best comps are the other units in its own building: when 2+ sold
    # in the year, they replace the neighbourhood comps.
    con.execute("""
    create table comp_bldg as
    select j.property_id, median(s.price / s.sqft) as comp_ppsf, count(*) as comp_n, 0.0 as comp_radius_m,
           arg_max(s.address, s.sold_date) as comp_nearest_addr, arg_max(s.price, s.sold_date) as comp_nearest_price, true as same_building
    from J j join S s on j.type_group = 'condo' and s.type_group = 'condo'
     and regexp_extract(s.address, '^(\\d+\\s+\\S+)', 1) = regexp_extract(j.address, '^(\\d+\\s+\\S+)', 1)
     and abs(s.x - j.x) < 150 and abs(s.y - j.y) < 150 and s.property_id <> j.property_id
    group by 1 having count(*) >= 2
    """)
    con.execute("create table comp2 as select * from comp_bldg union all select * from comp where property_id not in (select property_id from comp_bldg)")

    # ---- 4. Last deed, indexed to today --------------------------------------
    # County median of (sale price ÷ today's EMV) by sale year, from every
    # recorded dwelling sale. Last price × idx(now) / idx(sale year).
    con.execute("""
    create table idx as
    select p.county, p.is_condo, year(s.sale_date) as yr, median(s.sale_value / p.emv_total) as r, count(*) as n
    from cs.sale s join cty.parcels p on p.parcel_key = s.parcel_key
    where p.is_dwelling and p.emv_total > 50000 and s.sale_value > 30000 and year(s.sale_date) between 2005 and 2026
      and s.sale_value / p.emv_total between 0.2 and 3
    group by all having count(*) >= 200
    """)
    con.execute("create table idx_now as select county, is_condo, median(ratio) r from cty.sales group by all")

    # ---- 5. Signals --------------------------------------------------------
    con.execute(f"""
    create table permits as
    select 'hennepin' as county, APN as pin, permitNumber as num, permitType as ptype, workType as work, status,
           coalesce(nullif(trim(comments), ''), workType) as what, epoch_ms(issueDate)::date as issued,
           epoch_ms(completeDate)::date as completed, null::double as value
    from read_json_auto('{D / "mpls_permits.jsonl"}') where APN is not null
    union all
    select 'ramsey', json_extract_string(attrs, 'PIN'), json_extract_string(attrs, 'PERMITNUMBER'), json_extract_string(attrs, 'FOLDER_TYPE'), json_extract_string(attrs, 'WORK_TYPE'), json_extract_string(attrs, 'STATUS'),
           coalesce(json_extract_string(attrs, 'SUB_TYPE'), json_extract_string(attrs, 'FOLDER_TYPE')), event_at::date, epoch_ms(try_cast(json_extract_string(attrs, 'FINALDATE') as bigint))::date,
           try_cast(json_extract_string(attrs, 'EST_VALUE_OF_WORK') as double)
    from cs.record where layer = 'stp_permits' and event_at >= date '2024-01-01'
    """)
    con.execute(f"""
    create table cases as
    select split_part(parcel_key, ':', 2) as pin, json_extract_string(attrs, 'Case_Type') as ctype, max(json_extract_string(attrs, 'Case_Group')) as cgroup, json_extract_string(attrs, 'Violation_Case_Number') as num, min(epoch_ms(try_cast(json_extract_string(attrs, 'Completed_Date') as bigint)))::date as opened
    from cs.record where layer = 'mpls_caseviolations' and json_extract_string(attrs, 'Case_Type') in ('VBR', 'Nuisance')
      and try_cast(json_extract_string(attrs, 'Completed_Date') as bigint) >= 1672531200000
    group by all
    """)
    con.execute("create table vacant as select split_part(parcel_key, ':', 2) as pin, json_extract_string(attrs, 'VACANT_AS_OF') as vac_asof, json_extract_string(attrs, 'VB_CATEGORY') as vac_cat from cs.record where layer = 'stp_vacant_buildings'")
    con.execute(f"create table deedrisk as select * from read_csv_auto('{BRICKS / 'deed_risk_parcels.csv'}', types={{'pin': 'VARCHAR'}})")

    rows = _records(con, """
    select j.* exclude (pt), cal.cal_ratio, cal.cal_n, cal.cal_radius_m,
           comp.comp_ppsf, comp.comp_n, comp.comp_radius_m, comp.comp_nearest_addr, comp.comp_nearest_price, comp.same_building,
           i.r as idx_then, n.r as idx_now,
           (select list({'num': num, 'what': what, 'work': work, 'status': status, 'issued': issued, 'completed': completed, 'value': value, 'ptype': ptype} order by issued desc)
              from permits pm where pm.county = j.county and pm.pin = j.pin) as permits,
           (select list({'type': ctype, 'group': cgroup, 'num': num, 'opened': opened}) from cases c where j.county = 'hennepin' and c.pin = j.pin) as cases,
           (select any_value(vac_asof) from vacant v where j.county = 'ramsey' and v.pin = j.pin) as vacant_asof,
           (select any_value(vac_cat) from vacant v where j.county = 'ramsey' and v.pin = j.pin) as vacant_cat,
           (select any_value(exposure_flags) from deedrisk d where d.county = j.county and d.pin = j.pin) as deed_risk_flags
    from J j left join cal using (property_id) left join comp2 comp using (property_id)
    left join idx i on i.county = j.county and i.is_condo = j.county_condo and i.yr = year(j.deed_date)
    left join idx_now n on n.county = j.county and n.is_condo = j.county_condo
    """)

    # Minneapolis housing-code violations cited and still not fixed (Resolved_Date empty,
    # not abated, cancelled or transferred), per parcel. Nuisance (grass, trash) is not condition.
    open_viol = {}
    for line in open(D / "mpls_violations.jsonl"):
        v = json.loads(line)
        if v.get("Case_Type") in ("HIS", "FIS", "VBR") and not v.get("Resolved_Date") and (v.get("Status_Violation") or "").strip() not in ("Abate", "Cancel", "Transfer"):
            open_viol.setdefault(v["APN"], []).append((v.get("Violation_Code_Description") or "").strip())
    for r in rows:
        r["open_violations"] = open_viol.get(r["pin"], []) if r["county"] == "hennepin" else []
    global COND, VAL, SL
    COND = json.loads((D / "condition.json").read_text())
    VAL = _comp_grids(rows)
    from msp.valuation import SaleListModel
    SL = SaleListModel.load()
    out = [score(r) for r in rows]
    out.sort(key=lambda r: (r["rank_ok"], r["score"] if r["score"] is not None else -9), reverse=True)
    rank = 0
    for r in out:
        if r["rank_ok"]:
            rank += 1
            r["rank"] = rank
        else:
            r["rank"] = None
    con.execute("create table OUT as select * from read_json_auto(?)", [str(_dump(out, "out"))])
    con.execute(f"copy OUT to '{D / 'index.parquet'}' (format parquet)")
    con.execute(f"copy (select * exclude (permits, cases) from OUT order by rank nulls last) to '{D / 'index.csv'}' (header)")
    site = ROOT / "private"  # the full set; build_public.py gates what ships in site/
    site.mkdir(exist_ok=True)
    keep = ["rank", "property_id", "url", "address", "city", "zip", "county", "type_group", "beds", "baths", "sqft", "year_built", "ask", "original_ask",
            "dom", "n_cuts", "cut_pct", "pending", "score", "est_cal", "est_comp", "est_deed", "est_avm", "agree", "why", "exclusion",
            "evidence", "signals", "photo", "distressed", "hoa", "list_date", "tier", "tier_facts", "asis_ratio", "asis_n", "arv", "worth",
            "repair_ppsf", "repair_n", "repair_est", "spread_ppsf", "discounts_named", "n_estimates", "worth_est", "unexplained_gap", "cond_words", "room", "room_pct", "sl_ratio"]
    ranked = [{k: r.get(k) for k in keep if r.get(k) not in (None, [], "", False)} for r in out if r["rank_ok"]]
    # The list paints from data.json; evidence.json (the expanded view) loads after.
    heavy = ("evidence", "photo", "url")
    (site / "data.json").write_text(json.dumps({"built": TODAY.isoformat(), "total_listings": len(out), "condition": COND,
                                                "rows": [{k: v for k, v in r.items() if k not in heavy} for r in ranked]},
                                               separators=(",", ":"), default=str))
    (site / "evidence.json").write_text(json.dumps({r["property_id"]: [r.get(k) for k in heavy] for r in ranked},
                                                   separators=(",", ":"), default=str))
    summary(out)


def _comp_grids(rows):
    """Adjusted comp grid per listing (msp/valuation.py; accuracy in data/backtest.json)."""
    import sys
    sys.path.insert(0, str(ROOT))
    from msp import valuation
    grids, models = valuation.value_listings(rows)
    # One file per listing, gated like every per-listing layer (build_public.py PER_LISTING_DIRS).
    out = ROOT / "private" / "comps"
    import shutil
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    keep = ("address", "city", "photo", "url", "sold_date", "sold_price", "dist_m", "sqft", "beds", "baths", "year", "flags",
            "same_building", "same_subdivision", "time_adjusted", "lines", "adjusted", "weight", "emv")
    for pid, g in grids.items():
        slim = {k: g.get(k) for k in ("value", "emv_value", "emv_ratio", "comp_value", "model", "model_n", "month", "range")}
        slim["comps"] = [{k: c.get(k) for k in keep} for c in g["comps"]]
        (out / f"{pid}.json").write_text(json.dumps(slim, separators=(",", ":"), default=float))
    (D / "valuation_model.json").write_text(json.dumps(models.summaries(), indent=1))
    return grids


def _records(con, sql):
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _dump(rows, name):
    p = D / f"_tmp_{name}.json"
    p.write_text(json.dumps(rows, default=str))
    return p


def score(r):
    county = r["county"]
    ask = r["ask"]
    assessed = dt.date.fromisoformat(ASSESSED.get(county, "2026-01-02"))
    ev, sig, why = [], [], []

    # --- the three opinions ---
    est_cal = round(r["emv"] * r["cal_ratio"]) if r.get("emv") and r.get("cal_ratio") and r.get("cal_n", 0) >= 10 else None
    g = VAL.get(r["property_id"])
    est_comp = g["comp_value"] if g else round(r["comp_ppsf"] * r["sqft"]) if r.get("comp_ppsf") and r.get("sqft") else None
    if g:
        r["comp_n"], r["same_building"] = len(g["comps"]), any(c["same_building"] for c in g["comps"])
    est_deed = None
    deed_date = r.get("deed_date")
    if r.get("deed_price") and deed_date and r.get("idx_then") and r.get("idx_now"):
        dd = deed_date if isinstance(deed_date, dt.date) else dt.date.fromisoformat(str(deed_date)[:10])
        # Only a deed at least 2 years old: a recent purchase is usually the flip
        # this listing is reselling, and indexing it says nothing about the work.
        if dd.year >= 2005 and (TODAY - dd).days > 730 and r["deed_price"] >= 50000:
            est_deed = round(r["deed_price"] * r["idx_now"] / r["idx_then"])
            # A deed far from its year's norm against today's EMV was a bulk deed,
            # a family transfer, or a different house; it can't be brought forward.
            if r.get("emv") and not (0.6 <= est_deed / (r["emv"] * r["idx_now"]) <= 1.6):
                est_deed = None
    # The county and the last deed never see inside. When the listing's words measured as
    # needs-work (scripts/mine_condition.py), they take the same measured discount the comp
    # grid applies; the comps already did.
    ce = ((g or {}).get("subject", {}).get("cond") or (0.0, []))[0]
    if ce < 0:
        est_cal = round(est_cal * (1 + ce)) if est_cal else None
        est_deed = round(est_deed * (1 + ce)) if est_deed else None
    r["cond_effect"] = ce
    r["est_cal"], r["est_comp"], r["est_deed"] = est_cal, est_comp, est_deed
    r["est_avm"] = r.get("rdc_avm")
    # --- condition: every estimate above is an after-repair value (ARV) ---
    tier, facts = condition(r)
    r["tier"], r["tier_facts"] = tier, facts
    t = COND["tiers"].get(tier) if tier != "none" else None
    ratio = t["ratio"] if t else 1.0
    r["asis_ratio"], r["asis_n"] = (ratio, t["n"]) if t else (None, None)
    arvs = [e for e in (est_cal, est_comp, est_deed) if e]
    r["arv"] = round(statistics.median(arvs)) if arvs else None
    r["worth"] = round(r["arv"] * ratio) if r["arv"] else None
    # The records value (comps, county, last deed; condition-aware) is evidence. What the home
    # will sell for is predicted from how listings like it actually closed against their ask
    # (msp/valuation.py SaleListModel, data/sale_list_model.json, backtest in data/backtest.json).
    cond = (g or {}).get("subject", {}).get("cond") or (0.0, [])
    r["worth_est"], r["cond_words"] = r["worth"], cond[1]
    # Priced 30%+ under every estimate with nothing on record or in the listing to explain it:
    # closed sales say it will still sell near its ask; usually condition we can't see.
    r["unexplained_gap"] = bool(ask and r["worth_est"] and ask < 0.7 * r["worth_est"] and tier == "none" and cond[0] >= 0)
    if SL and ask:
        from msp.valuation import sl_inputs, area_of
        f = sl_inputs(ask, r["worth_est"], r.get("dom"), r.get("n_cuts"), r.get("original_ask"), r.get("relisted"), r.get("type_group"),
                      area_of(r), TODAY.month, cond[0], r.get("hoa"))
        r["sl_ratio"] = round(SL.ratio(f), 4)
        r["worth"] = round(ask * r["sl_ratio"])
        r["room"] = ask - r["worth"]
        r["room_pct"] = round(r["room"] / ask, 4)
    if t:
        rp = COND["tiers"]["heavy" if tier == "condemned" else tier]
        r["repair_ppsf"], r["repair_n"], r["spread_ppsf"] = rp["repair_ppsf"], rp["repair_n"], rp["spread_ppsf"]
        r["repair_est"] = round(rp["repair_ppsf"] * r["sqft"]) if r.get("sqft") else None
    named = [(n, e) for n, e in (("County", est_cal), ("Comps", est_comp), ("Last sale", est_deed), ("Realtor.com", r.get("rdc_avm"))) if e]
    ests = [e * ratio for _, e in named]
    disc = [1 - ask / e for e in ests] if ask else []
    r["discounts_named"] = [{"name": n, "arv": e, "asis": round(e * ratio), "disc": round(1 - ask / (e * ratio), 3)} for n, e in named] if ask else []
    r["n_estimates"] = len(ests)
    if len(disc) >= 2:
        # Agreement of the records estimates: evidence beside the prediction.
        sd = statistics.pstdev(disc)
        r["agree"] = round(1 - min(1, sd / 0.25), 2)
        # The ranking key is room to negotiate: ask minus predicted sale, as a share of the ask.
        r["score"] = r.get("room_pct")
    else:
        r["score"], r["agree"] = None, None
    r["discounts"] = [round(x, 3) for x in disc]

    # --- signals ---
    cut_pct = round(1 - ask / r["original_ask"], 3) if r.get("original_ask") and ask and r["original_ask"] > ask else 0
    r["cut_pct"] = cut_pct
    permits = r.get("permits") or []
    after = [p for p in permits if p["issued"] and _d(p["issued"]) >= assessed]
    finished = [p for p in after if (p["status"] or "").lower() in ("closed", "finaled", "final") or p["completed"]]
    open_ = [p for p in after if p not in finished and (p["status"] or "").lower() not in ("cancelled", "expired", "withdrawn")]
    wreck = [p for p in permits if (p["ptype"] or "").lower().startswith("wreck") or "demolition" in (p["ptype"] or "").lower()]
    for p in finished:
        sig.append(f"finished permit {p['issued']}: {p['what'][:60]} (after the {assessed.year} assessment)")
    for p in open_:
        sig.append(f"open permit {p['issued']}: {p['what'][:60]}")
    for c in r.get("cases") or []:
        sig.append(f"Minneapolis {'vacant building' if c['type'] == 'VBR' else 'nuisance'} case {c['num']} ({c['opened']})")
    if r.get("vacant_asof"):
        sig.append(f"on Saint Paul's vacant building list (as of {r['vacant_asof']})")
    if r.get("deed_risk_flags"):
        sig.append("on the deed-theft exposure list (long-held, owner mails elsewhere)")
    if r.get("distress"):
        sig.append(f"listing text: {r['distress']}")
    if r.get("foreclosure"):
        sig.append("foreclosure")
    if r.get("senior_text"):
        sig.append("55+ / age-restricted")
    if r.get("short_sale"):
        sig.append("short sale")
    r["signals"] = sig
    r["distressed"] = bool(r.get("vacant_asof") or r.get("distress") or r.get("short_sale") or r.get("foreclosure")
                           or any(c["type"] == "VBR" for c in r.get("cases") or []))

    # --- exclusions: rows that would rank for the wrong reason ---
    ex = None
    tg = r.get("type_group")
    if tg == "land" or (not r.get("sqft") and not r.get("beds")):
        ex = "lot or land, no house to value"
    elif r.get("pending"):
        ex = "under contract (pending/contingent)"
    elif tg == "mobile":
        ex = "mobile home: county values the lot or the park, not the home"
    elif r.get("auction_text"):
        ex = "auction: the price is an opening bid"
    elif not r.get("pin"):
        ex = "no county parcel matched"
    elif not ask or ask < 20000:
        ex = "no real asking price"
    elif r.get("new_construction") or (r.get("year_built") and r["year_built"] >= assessed.year - 1) or r.get("emv_bldg") == 0 or \
            (r.get("emv_bldg") is not None and ask and r["emv_bldg"] < 0.3 * ask and (r.get("year_built") or 0) >= 2015):
        ex = "new or rebuilt house: the assessment predates it"
    elif r.get("restricted_text"):
        ex = "income-restricted or land-trust home: the price is capped, not discounted"
    elif r.get("coop_text"):
        ex = "co-op: the price excludes the building's shared debt"
    elif tg in ("condo", "townhome") and not (r.get("unit_match") or r.get("emv_exact")) and \
            ((r.get("site_units") or 1) > 1 or tg == "condo" or "address" not in (r.get("match_how") or "")):
        ex = "unit not resolved to its own parcel (its assessment didn't match any parcel in the building)"
    elif r.get("emv") and not (1 / 3 <= r["emv"] / ask <= 3):
        # Realtor's "assessment" is often the taxable value, not EMV, so it can't
        # vet a match. A 3x gap between ask and EMV is a wrong parcel, a lot, or
        # a whole building, not a bargain.
        ex = f"parcel match doubtful: county EMV {money(r['emv'])} against an ask of {money(ask)}"
    elif wreck or r.get("teardown_text") or (r.get("emv") and r.get("emv_bldg") is not None and r["emv_bldg"] < 0.15 * r["emv"]):
        ex = "teardown / land value" + (f" (wrecking permit {wreck[0]['issued']})" if wreck else "")
    elif r.get("multiparcel_text") or (r.get("lot_sqft") and r.get("parcel_sqft") and r["lot_sqft"] > 2 * r["parcel_sqft"] and r["lot_sqft"] > 15000):
        ex = "multi-parcel listing: one parcel's value can't price it"
    elif r["score"] is None:
        ex = "fewer than two value opinions"
    r["exclusion"] = ex
    r["rank_ok"] = ex is None

    # --- evidence (expandable) and the why line ---
    if ce < 0:
        ev.append(f"Condition: the listing reads like {', '.join(((g or {}).get('subject', {}).get('cond') or (0, []))[1])}; sales whose listings used those words sold "
                  f"{ce:+.1%} against their comps, so County and Last sale below carry that discount")
    if est_cal:
        ev.append(f"County: EMV {money(r['emv'])} ({county.title()} {assessed.year} assessment, parcel {r['pin']}) × {r['cal_ratio']:.2f}, "
                  f"the median sale÷EMV of the {r['cal_n']} nearest recorded sales at a similar EMV (within {r['cal_radius_m']/1609:.1f} mi) = {money(est_cal)}" + (f" after the condition discount" if ce < 0 else ""))
    if est_comp and g:
        ev.append(f"Comps: the {len(g['comps'])} most similar sales, each brought to {g['month']} with the {g['model']} price index and adjusted for "
                  f"every difference at rates measured from {g['model_n']:,} sales, weighted toward the least-adjusted = {money(est_comp)} "
                  f"(adjusted range {money(g['range'][0])} to {money(g['range'][1])})")
    elif est_comp:
        where = "in this building" if r.get("same_building") else f"within {r['comp_radius_m']/1609:.1f} mi"
        ev.append(f"Comps: {r['comp_n']} similar {PLURAL.get(tg, 'homes')} sold in the last year {where} at a median "
                  f"${r['comp_ppsf']:.0f}/sqft × {r['sqft']:,} sqft = {money(est_comp)} (nearest: {r['comp_nearest_addr']}, {money(r['comp_nearest_price'])})")
    if est_deed:
        ev.append(f"Last deed: {money(r['deed_price'])} in {str(r['deed_date'])[:7]}, × {r['idx_now']/r['idx_then']:.2f} "
                  f"{county.title()} price change since (sale÷EMV {r['idx_then']:.2f} then, {r['idx_now']:.2f} now) = {money(est_deed)}")
    if r.get("asis_ratio") and r.get("arv"):
        tt = COND["tiers"]["heavy" if r["tier"] == "condemned" else r["tier"]]
        src = (f"{tt['stp_n']} Saint Paul flips with ${COND['light_max_ppsf']}+/sqft of permitted work and {tt['mpls_n']} Minneapolis houses bought with a vacant-building case open"
               if r["tier"] != "light" else f"{tt['stp_n']} Saint Paul flips with permitted work under ${COND['light_max_ppsf']}/sqft")
        ev.append(f"Condition {r['tier']}: " + "; ".join(r["tier_facts"]) + ".")
        ev.append(f"Worth as-is: the estimates above are after-repair values. Their middle, {money(r['arv'])}, × {r['asis_ratio']:.2f} = {money(r['worth_est'])}. "
                  f"{r['asis_ratio']:.2f} is the median buy price ÷ resale price of {r['asis_n']} houses sold and resold within 3-24 months for more ({src})"
                  + (". No condemned house in the records was flipped, so this uses the heavy ratio" if r["tier"] == "condemned" else "") + ".")
        if r.get("repair_est"):
            ev.append(f"Repair cost: those flips filed permits worth a median ${r['repair_ppsf']:.0f}/sqft (n={r['repair_n']}, Saint Paul) × {r['sqft']:,} sqft = {money(r['repair_est'])}. "
                      f"Owners under-declare permit values: the same flips resold for ${r['spread_ppsf']:.0f}/sqft more than they were bought for.")
    if r.get("sl_ratio"):
        ev.append(f"Likely sells for {money(r['worth'])}: {r['sl_ratio'] - 1:+.1%} against the ask, predicted from how {SL.n:,} closed Twin Cities sales went "
                  f"against their last ask given days listed ({r.get('dom')}), price cuts ({r.get('n_cuts') or 0}), the ask against the records value "
                  f"({money(r['worth_est'])}), area, price, type, season, listing words and HOA. Room to negotiate: {money(abs(r['room']))} "
                  + ("under the ask" if r['room'] >= 0 else "OVER the ask: expect to pay more"))
    if r.get("unexplained_gap"):
        ev.append("Asking far below every estimate with no reason on record. Usually condition we can't see.")
    if r.get("rdc_avm"):
        ev.append(f"Realtor.com estimate (avg of {r['rdc_avm_n']}): {money(r['rdc_avm'])}")
    if r.get("hoa"):
        ev.append(f"HOA ${r['hoa']:,.0f}/month")
    if r.get("deed_price"):
        ev.append(f"Last recorded sale: {money(r['deed_price'])} on {str(r['deed_date'])[:10]}")
    if r.get("original_ask") and r["original_ask"] != ask:
        ev.append(f"Listed at {money(r['original_ask'])}; {r['n_cuts']} price cut{'s' if r['n_cuts'] != 1 else ''} to {money(ask)} ({cut_pct:.0%})")
    r["evidence"] = ev

    parts = [f"asking {money(ask)}"] + ([f"likely sells for ~{money(r['worth'])} ({'room ' + money(r['room']) if r['room'] >= 0 else 'expect to pay ' + money(-r['room']) + ' over'})"] if r.get("sl_ratio") else [])
    if r.get("asis_ratio") and r.get("worth"):
        parts.append(f"needs {'heavy' if r['tier'] != 'light' else 'light'} work ({r['tier_facts'][0].lower()}): records say ~{money(r['worth_est'])} as-is, {money(r['arv'])} fixed up")
    if est_comp:
        parts.append(f"{r['comp_n']} {'units in this building' if r.get('same_building') else 'similar homes nearby'} sold for ~{money(est_comp)}")
    if est_cal:
        parts.append(f"EMV {money(r['emv'])} ×{r['cal_ratio']:.2f} local ratio = {money(est_cal)}")
    if est_deed:
        parts.append(f"last sold {money(r['deed_price'])} in {str(r['deed_date'])[:4]} ≈ {money(est_deed)} today")
    if r.get("hoa") and r["hoa"] >= 500:
        parts.append(f"HOA ${r['hoa']:,.0f}/mo")
    if r.get("n_cuts"):
        parts.append(f"{r['n_cuts']} price cut{'s' if r['n_cuts'] != 1 else ''}")
    if r.get("dom") and r["dom"] >= 60:
        parts.append(f"{r['dom']} days listed")
    if finished:
        parts.append(f"{finished[0]['what'][:40].lower()} permit {str(finished[0]['issued'])[:4]} not in assessment")
    if open_:
        parts.append(f"open permit: {open_[0]['what'][:40].lower()}")
    if r.get("vacant_asof") or any(c["type"] == "VBR" for c in r.get("cases") or []):
        parts.append("registered vacant building")
    if r.get("short_sale"):
        parts.append("short sale")
    if r.get("foreclosure"):
        parts.append("foreclosure")
    if r.get("distress"):
        parts.append(f"listing says {r['distress'].split(', ')[0]}")
    r["why"] = "; ".join(parts)
    return r


def condition(r):
    """Tier (none / light / heavy / condemned) and the facts that set it, worst first."""
    facts = {"condemned": [], "heavy": [], "light": []}
    cat = str(r.get("vacant_cat") or "")
    if cat:
        facts[{"3": "condemned", "2": "heavy"}.get(cat, "light")].append(
            f"Saint Paul vacant building list, category {cat}" + (f", vacant since {r['vacant_asof']}" if r.get("vacant_asof") else ""))
    for c in r.get("cases") or []:
        if c["type"] == "VBR":
            cond = c.get("group") == "CVBR"
            facts["condemned" if cond else "heavy"].append(f"Minneapolis {'condemned ' if cond else ''}vacant building case {c['num']}, opened {c['opened']}")
    ov = r.get("open_violations") or []
    if len(ov) >= 3:
        facts["light"].append(f"{len(ov)} Minneapolis housing violations cited and not fixed")
    for t, phrases in (r.get("cond_text") or {}).items():
        for ph in phrases:
            facts[t].append(f'Listing says "{ph}"')
    for t in ("condemned", "heavy", "light"):
        if facts[t]:
            return t, facts[t] + [f for u in ("condemned", "heavy", "light") if u != t for f in facts[u]]
    return "none", []


def _d(x):
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def summary(out):
    import collections
    print("listings", len(out), "ranked", sum(r["rank_ok"] for r in out))
    print("by county", collections.Counter(r["county"] for r in out).most_common())
    print("exclusions", collections.Counter((r["exclusion"] or "").split(":")[0].split(" (")[0] for r in out).most_common())
    for f in ["pin", "emv", "est_cal", "est_comp", "est_deed", "est_avm", "has_detail", "permits", "signals"]:
        print(f"  {f}: {sum(1 for r in out if r.get(f))}")
    for r in out[:20]:
        print(r["rank"], r["score"], r["address"], r["city"], "|", r["why"])


if __name__ == "__main__":
    main()
