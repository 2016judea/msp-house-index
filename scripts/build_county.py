"""County side of the index: every parcel in the 7 metro counties with its
current assessment, plus the recorded sales used to calibrate it.

Reads bricks' DuckDB files READ-ONLY (other sessions share them) and writes
data/county.duckdb here. Never writes into bricks.

Sources:
  closed_system.duckdb  parcel (geometry, EPSG:26915) + parcel_snapshot vintage
                        'metrogis_current' (EMV land/bldg/total, units, homestead,
                        use class, year built, last sale) for all 7 counties
  hennepin_gis.duckdb   Hennepin's own sale_code (arm's-length flag) and a newer
                        sale month than MetroGIS carries
"""
import duckdb
from pathlib import Path

BRICKS = Path.home() / "Desktop/workspace/bricks/data"
OUT = Path(__file__).resolve().parent.parent / "data" / "county.duckdb"

# Use classes that are a dwelling a household buys (per county vocabulary, see
# bricks docs/HANDOVER-twin-cities-cre.md: each county writes USECLASS1 differently).
RESIDENTIAL = r"(?i)(^residential$|townhouse|condo|double bungalow|triplex|lake ?shore|residential single unit|residential 1.?(to|-).?3|duplex|^1a|^201 |^(100|105|110|140)$|residential-)"
NOT_DWELLING = r"(?i)(vacant|unimproved|storage|garage|apartment|commercial|industrial|agricultur|farm)"


def main():
    OUT.unlink(missing_ok=True)
    con = duckdb.connect(str(OUT))
    con.execute("install spatial; load spatial")
    con.execute(f"attach '{BRICKS / 'closed_system.duckdb'}' as cs (read_only)")
    con.execute(f"attach '{BRICKS / 'hennepin_gis.duckdb'}' as hn (read_only)")
    con.execute(f"""
    create table parcels as
    select p.county, p.pin, p.parcel_key, p.geom, p.lon, p.lat, p.area_sqft,
           coalesce(p.is_condo, false) as is_condo,
           -- Dakota writes -9999 for "no value" (16,024 parcels): a sentinel read as a
           -- number makes emv_bldg < 0.15*emv true and tags the house a teardown.
           nullif(s.emv_land, -9999) as emv_land, nullif(s.emv_bldg, -9999) as emv_bldg,
           nullif(s.emv_total, -9999) as emv_total, s.homestead, s.num_units, s.fin_sq_ft,
           nullif(s.year_built, 0) as year_built, trim(s.useclass1) as useclass,
           s.anumber, s.street, s.city, s.zip,
           nullif(ltrim(upper(trim(json_extract_string(s.attrs, 'SUB_ID1'))), '0'), '') as unit,
           s.sale_date, s.sale_value,
           regexp_matches(coalesce(s.useclass1,''), '{RESIDENTIAL}') and not regexp_matches(coalesce(s.useclass1,''), '{NOT_DWELLING}') as is_dwelling
    from cs.parcel p
    join cs.parcel_snapshot s on s.parcel_key = p.parcel_key and s.vintage = 'metrogis_current'
    qualify row_number() over (partition by p.parcel_key order by s.mkt_year desc nulls last) = 1
    """)
    # Hennepin: its own layer carries a newer sale month and the arm's-length code.
    con.execute("""
    create table hn_sales as
    select pin, sale_price, strptime(sale_date || '01', '%Y%m%d')::date as sale_month, sale_code,
           try_cast(market_value as double) as emv, PR_TYP_NM1 as hn_type
    from hn.parcels where sale_price > 0 and sale_date >= '202401'
    """)
    # Multi-parcel deeds outside Hennepin: same price on the same day on >1 parcel.
    con.execute("""
    create table sales as
    with base as (
      select p.county, p.pin, p.geom, p.lon, p.lat, p.emv_total as emv, p.year_built, p.useclass, p.is_condo,
             coalesce(h.sale_month, p.sale_date::date) as sale_date,
             coalesce(h.sale_price, p.sale_value) as price,
             h.sale_code
      from parcels p left join hn_sales h on p.county = 'hennepin' and h.pin = p.pin
      where p.is_dwelling
    ), shared as (
      select county, sale_date, price, count(*) as n from base group by all
    )
    select b.*, b.price / b.emv as ratio
    from base b join shared s using (county, sale_date, price)
    where b.sale_date >= date '2025-01-01' and b.price >= 50000 and b.emv >= 50000
      and s.n = 1                                                  -- one deed, one parcel
      and (b.county <> 'hennepin' or b.sale_code = 'WARRANTY DEED') -- Hennepin's arm's-length code
      and (b.year_built is null or b.year_built < year(b.sale_date) - 1)  -- EMV of a half-built house is not a yardstick
      and b.price / b.emv between 0.5 and 2.0                      -- trims family transfers and teardowns
    """)
    for r in con.execute("select county, count(*), round(median(ratio),3), round(quantile_cont(ratio,0.1),3), round(quantile_cont(ratio,0.9),3) from sales group by 1 order by 1").fetchall():
        print("sales", r)
    print("parcels", con.execute("select count(*), count(*) filter (where is_dwelling) from parcels").fetchall())
    con.execute("create index sales_county on sales(county)")


if __name__ == "__main__":
    main()
