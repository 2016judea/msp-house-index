#!/usr/bin/env python3
"""What the market pays for a house that needs work, measured from flips.

    python scripts/build_condition.py    # writes data/condition.json (read by build_index.py)

Every value the index computes is an AFTER-REPAIR value: comps are ordinary homes
that sold, and the county values a habitable house. A gutted house sells for a
fraction of that. This script measures the fraction from recorded deeds, never
from a guess:

  Flip pair  the same house (not a condo, not a new build) recorded as sold, then
             sold again 3-24 months later for more. Resale = the after-repair price,
             buy = the as-is price. buy / resale is the as-is ratio.
  Tier       how much work went in between, from the public record:
               Saint Paul: building, electrical, plumbing and mechanical permits filed
               between the two sales, EST_VALUE_OF_WORK summed ÷ the county's
               finished sqft. Under $30/sqft = light, $30+/sqft = heavy.
               Minneapolis: a vacant-building (VBR) case opened in the 3 years before
               the purchase = heavy. Minneapolis permits carry no valuation.
  Repair     the median permit $/sqft in each tier ("permits filed say"), with the
             flip spread (resale - buy) per sqft beside it as the cross-check:
             owners under-declare the value of work, so the spread runs higher.
  Second     sales of a Minneapolis house while it had an open VBR case, against
  source     ordinary Minneapolis house sales the same years, both as price ÷ the
             parcel's current EMV. Independent of the flip pairs' resale price.

Inputs (read-only): data/county.duckdb, bricks closed_system.duckdb (sale history,
stp_permits, mpls_caseviolations, mpls_condemned).
"""
import json, datetime as dt
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parent.parent
BRICKS = Path.home() / "Desktop/workspace/bricks/data"
LIGHT_MAX = 30  # $/sqft of permitted work: below = light, at or above = heavy

c = duckdb.connect()
c.execute(f"attach '{ROOT / 'data/county.duckdb'}' as cty (read_only)")
c.execute(f"attach '{BRICKS / 'closed_system.duckdb'}' as cs (read_only)")

# One price per parcel per month (the sale history repeats a deed once per snapshot).
c.execute("""create table s2 as select s.parcel_key, date_trunc('month', s.sale_date) m, max(s.sale_value) v
 from cs.sale s join cty.parcels p using (parcel_key)
 where p.is_dwelling and not p.is_condo and s.sale_value >= 20000
   and s.sale_date between '2015-07-01' and current_date
   and (p.year_built is null or p.year_built < year(s.sale_date) - 1)   -- a new build on a bought lot is not a flip
 group by all""")
c.execute("""create table pr as select * from (
   select parcel_key, m, v, lead(m) over w m2, lead(v) over w v2 from s2 window w as (partition by parcel_key order by m))
 where m2 is not null and date_diff('month', m, m2) between 3 and 24 and v2 > v""")

# Saint Paul: permits between the sales. Its register stops 2025-06-30, so only resales before then.
c.execute("""create table sp as select parcel_key, event_at::date d, try_cast(json_extract_string(attrs, 'EST_VALUE_OF_WORK') as double) val
 from cs.record where layer = 'stp_permits' and parcel_key is not null""")
c.execute("""create table stp as select pr.*, p.fin_sq_ft sqft, coalesce(sum(sp.val), 0) wk, count(sp.d) np
 from pr join cty.parcels p using (parcel_key)
 left join sp on sp.parcel_key = pr.parcel_key and sp.d between pr.m - interval 1 month and pr.m2
 where pr.parcel_key like 'ramsey:%' and p.city ilike '%paul%' and pr.m2 <= date '2025-06-30' and p.fin_sq_ft > 300
 group by all""")
c.execute("""create table stp_t as select *, case when np = 0 then 'none' when wk / sqft < %d then 'light' else 'heavy' end tier from stp""" % LIGHT_MAX)

# Minneapolis: a VBR case opened within 3 years before the purchase.
c.execute("""create table vb as
 select parcel_key, min(epoch_ms(try_cast(json_extract_string(attrs, 'Completed_Date') as bigint)))::date d0,
        bool_or(json_extract_string(attrs, 'Case_Group') = 'CVBR') cond
 from cs.record where layer = 'mpls_caseviolations' and json_extract_string(attrs, 'Case_Type') = 'VBR'
 group by parcel_key, json_extract_string(attrs, 'Violation_Case_Number')""")
c.execute("""create table mpls as select pr.*, bool_or(vb.cond) cond from pr
 join vb on vb.parcel_key = pr.parcel_key and vb.d0 between pr.m - interval 36 month and pr.m + interval 1 month
 where pr.parcel_key like 'hennepin:%' group by all""")


def one(sql):
    return c.execute(sql).fetchone()


out = {"built": dt.date.today().isoformat(), "light_max_ppsf": LIGHT_MAX, "tiers": {}}
for tier in ("light", "heavy"):
    n, r, q1, q3, ppsf, spread, wk = one(f"""select count(*), median(v / v2), quantile_cont(v / v2, .25), quantile_cont(v / v2, .75),
        median(wk / sqft), median((v2 - v) / sqft), median(wk) from stp_t where tier = '{tier}'""")
    out["tiers"][tier] = {"stp_n": n, "stp_ratio": round(r, 3), "stp_iqr": [round(q1, 3), round(q3, 3)],
                          "repair_ppsf": round(ppsf, 1), "repair_n": n, "spread_ppsf": round(spread, 1), "repair_median": round(wk)}
n, r = one("select count(*), median(v / v2) from mpls")
out["tiers"]["heavy"].update(mpls_n=n, mpls_ratio=round(r, 3))
# The ratio used: Saint Paul's permit-tiered pairs, pooled with Minneapolis's VBR pairs for heavy.
out["tiers"]["light"]["ratio"], out["tiers"]["light"]["n"] = out["tiers"]["light"]["stp_ratio"], out["tiers"]["light"]["stp_n"]
r, n = one("""select median(x), count(*) from (select v / v2 x from stp_t where tier = 'heavy' union all select v / v2 from mpls)""")
out["tiers"]["heavy"]["ratio"], out["tiers"]["heavy"]["n"] = round(r, 3), n
n_none, r_none = one("select count(*), median(v / v2) from stp_t where tier = 'none'")
out["no_permit_pairs"] = {"n": n_none, "ratio": round(r_none, 3)}
# Condemned: count the pairs that exist; too few to measure means it borrows heavy.
n_cond = one("select count(*) from mpls where cond")[0]
out["tiers"]["condemned"] = {"n": n_cond, "ratio": out["tiers"]["heavy"]["ratio"] if n_cond < 10 else
                             round(one("select median(v / v2) from mpls where cond")[0], 3),
                             "borrowed": n_cond < 10}

# Second source: Minneapolis house sales made while a VBR case was open, price ÷ current EMV,
# against all Minneapolis house sales in the same years.
c.execute("""create table vsale as select s.parcel_key, s.m, s.v, p.emv_total from s2 s join cty.parcels p using (parcel_key)
 join vb on vb.parcel_key = s.parcel_key and vb.d0 between s.m - interval 36 month and s.m + interval 1 month
 where s.parcel_key like 'hennepin:%' and p.city ilike 'minneapolis' and p.emv_total > 50000 and s.m >= date '2021-01-01'
 group by all""")
nv, rv = one("select count(*), median(v / emv_total) from vsale")
na, ra = one("""select count(*), median(s.v / p.emv_total) from s2 s join cty.parcels p using (parcel_key)
 where s.parcel_key like 'hennepin:%' and p.city ilike 'minneapolis' and p.emv_total > 50000 and s.m >= date '2021-01-01'""")
out["vacant_sales"] = {"n": nv, "ratio_to_emv": round(rv, 3), "all_n": na, "all_ratio_to_emv": round(ra, 3),
                       "relative": round(rv / ra, 3)}

(ROOT / "data/condition.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
