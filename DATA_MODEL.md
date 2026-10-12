# Data model: the ideal per-listing record

Written 2026-10-11 from the need alone, before looking at any data we hold
(`~/.claude/BUSINESS.md` rule 5). The need: *every active home listing in the
Minneapolis–Saint Paul 7-county metro, ranked by how far its asking price sits
below what the house is worth, with the evidence for each row.*

Reality check against this model is at the bottom, added after probing.

## Entities

```
Listing ──1:1── Property ──1:N── Parcel (normally 1; >1 = multi-parcel listing, excluded)
   │               │
   │               ├──1:N── Assessment (by tax year)
   │               ├──1:N── RecordedSale (deed)
   │               ├──1:N── Permit
   │               ├──1:N── Violation / Condition flag
   │               └──N:1── Area (neighbourhood/city × price tier → sale/EMV ratio)
   │
   ├──1:N── ListingEvent (listed, price change, pending, relisted, delisted)
   └──1:N── ValueEstimate (county-calibrated, our comps, portal AVMs)
                └── Opportunity (one per listing; the sort key)
```

## Listing — *is it for sale, and at what?*

| field | question it answers |
|---|---|
| listing_id | which listing (stable across refreshes) |
| source_url | where can I see it |
| status | active, or already pending/contingent? |
| list_price | what is being asked |
| list_date / days_on_market | how long has it sat (stale = negotiable) |
| original_list_price, n_price_cuts, total_cut_pct | is the seller already moving |
| last_relist_date | was it pulled and re-listed to reset DOM |
| listing_type | standard / foreclosure / short sale / new construction / auction |
| hoa_monthly | what the ask hides in carrying cost |
| brokerage / agent | who to call |

## Property — *what is it, physically?*

| field | question |
|---|---|
| address, city, county, zip, lat, lon | where |
| apn / pin | which county parcel (the join key) |
| property_type | single family / townhouse / condo / multi-family / land |
| beds, baths, sqft (finished above + below), lot_sqft, year_built | size and age, for comparables |
| condition / effective year | has it been gutted and redone, or is it original |

## Parcel / Assessment — *what does the county say it's worth?*

| field | question |
|---|---|
| emv_total, emv_land, emv_building | county's estimated market value |
| assessment_date (valuation date, e.g. Jan 2 of tax year) | how old is that opinion |
| county_property_type, homestead | is the parcel the same kind of thing as the listing (condo unit vs building) |
| units_on_parcel | catches a condo matched to a whole building |

## RecordedSale — *what did it, and its neighbours, actually sell for?*

| field | question |
|---|---|
| sale_date, sale_price | last arm's-length price of this house |
| arms_length flag / instrument type | is the price real (not a quit-claim, foreclosure, family transfer) |
| emv_at_sale | ratio input: sale_price ÷ EMV for calibration |

## Area — *how wrong is the assessor here?*

| field | question |
|---|---|
| area_key (city or neighbourhood) × price_tier | the cell |
| median sale/EMV ratio, n_sales, window | multiply EMV by this to get a market value |

## Permit — *has the house changed since it was valued?*

| field | question |
|---|---|
| permit_type, description, valuation | what work |
| issued_date, finaled_date, status | done (upside the EMV missed) or open (risk) |
| after_assessment | was it pulled after the valuation date |

## Violation / Condition flag — *what's wrong with it?*

| field | question |
|---|---|
| code violations (open/closed, date) | known defects |
| vacant / condemned / boarded registration | is it habitable |
| deed-risk flag | title risk |
| teardown signal (land value ≥ most of EMV, demolition permit) | is the ask for the dirt |

## ValueEstimate — *three independent opinions of value*

| field | question |
|---|---|
| county_calibrated = EMV × area ratio (+ n behind the ratio) | the assessor, corrected for its local bias |
| comp_value = median $/sqft of similar nearby recent sales × sqft (+ n comps, radius) | what similar homes nearby actually fetch |
| portal_estimates (each AVM, named) | a third opinion |

## Opportunity — *the sort key*

| field | question |
|---|---|
| gap_vs_each_estimate (%) | how far below each opinion is the ask |
| agreement (dispersion of the estimates) | do the opinions agree, or is one an outlier |
| score | discount weighted by agreement and by how many estimates exist |
| why | one plain line a person can check |
| exclusion_reason | why a row is not ranked (lot-only, teardown, multi-parcel, condo/building mismatch, no value) |

---

## Reality check (added 2026-10-11, after probing; 14,462 unique listings)

| field | filled | source / note |
|---|---|---|
| listing, ask, list date, status, type, beds/baths/sqft/lot/year | 14,462 (sqft 13,531) | Realtor.com `home_search`, all 7 counties |
| original ask, price cuts, relist | 13,577 / 5,823 with a cut | Realtor `property_history` (NorthstarMLS events) |
| apn / pin | **0 from Realtor** (`tax_record.apn` null) → 14,371 by join | point-in-parcel + house number/street + **unit number** (MetroGIS `SUB_ID1`); 1,285 of 1,470 condos resolved to their own unit |
| EMV total / land / building | 14,362 | MetroGIS current snapshot via bricks `closed_system.duckdb` |
| assessment date | by county | Hennepin, Dakota, Scott, Washington, Carver: Jan 2 2026; Ramsey, Anoka: Jan 2 2025 (MetroGIS `MKT_YEAR`) |
| units on parcel | partial | counted from identical-centroid parcels; Hennepin `NUM_UNITS` is 0 on houses |
| arm's-length flag | **Hennepin only** | `sale_code = WARRANTY DEED`; other counties publish no code, so single-parcel deed + ratio band 0.5–2.0 stand in |
| sale/EMV ratio by area × tier | 13,304 | 32,621 qualifying 2025–26 deeds across all 7 counties; every county publishes sales |
| comp value | 12,694 | 49,444 Realtor closed sales, last 12 months (county records carry no finished sqft for Hennepin) |
| last deed, indexed | 6,897 | county deed ≥2 years old, county × condo index |
| **portal AVMs** | **513 of 14,462 (3.5%)** | Realtor's `estimates.current_values` is null for almost every MN listing. **Unfilled**; shown where present, not relied on |
| permits after assessment | 203 listings | Minneapolis live (CCS_Permits); **Saint Paul only through 2025-06-30** (its register stopped); **no permits for the other ~100 cities** |
| code violations | 362 listings | Minneapolis vacant-building and nuisance cases only |
| vacant / condemned | 17 + Mpls VBR cases | Saint Paul vacant list current; **Minneapolis condemned/boarded layers in bricks end 2013–2016, not used** |
| deed-risk flag | 290 | bricks `deed_risk_parcels.csv` (no Dakota) |
| condition / effective year | **unfilled** | no public source; listing-text keywords stand in as a signal, never as a number |
| finished sqft from the county | Hennepin **0**, others ~80% | so comps use MLS sqft |

**Call: build, not reframe.** Every field the score depends on (ask, EMV,
local ratio, comps) fills for ≥88% of listings. The unfilled fields (portal AVMs,
suburban permits, condition) are signals, and their absence is stated on the row
rather than imputed.

---

## Condition: what is it worth *as it stands*? (added 2026-10-11, written before probing)

Every value above is an **after-repair value** (ARV): comps are ordinary homes that
sold, and the county values a habitable house. For a gutted or condemned house the
gap between ask and ARV is mostly the rehab, not a bargain. The ideal record adds:

### ConditionTier — *how much work does it need?*

| field | question it answers |
|---|---|
| tier (none / light / heavy / condemned) | how far from move-in is it |
| tier_facts[] (record or listing phrase, date, source) | which facts set the tier, so a person can check them |
| open_violations_n | how many cited defects are still unfixed |

### ConditionDiscount (per area × tier) — *what does the market pay for a house in this state?*

| field | question |
|---|---|
| asis_ratio = median(buy price ÷ resale price) over flip pairs | what fraction of ARV a house needing work actually sells for |
| n_pairs, window, area | how much evidence, from where |
| vacant_sale_ratio = median(sale ÷ ARV) for parcels sold while on a vacant list / case | a second, independent measure |

### RepairCost (per tier) — *what does the work cost?*

| field | question |
|---|---|
| permit_value_sum per parcel, in the window after vacancy or purchase | what the owners told the city the work cost |
| $/sqft median by tier, n | scales to this house |
| flip spread (resale − buy) | cross-check: permit valuations run low |

### ValueEstimate additions

| field | question |
|---|---|
| arv (the current "worth about") | what it's worth fixed up |
| worth_as_is = arv × asis_ratio(tier) | what it's worth today, the number the ranking should use |

### Reality check for condition (2026-10-11, `scripts/build_condition.py` → `data/condition.json`)

| field | filled | source / note |
|---|---|---|
| tier | 167 of 7,262 ranked rows (141 light, 25 heavy, 1 condemned) | StP vacant category (1 light, 2 heavy, 3 condemned); Mpls VBR case (heavy), CVBR (condemned); ≥3 unfixed Mpls housing violations (light); listing phrases, each checked against real listings (bare "as is", "gutted", "shell" are mostly false positives and only count in narrow forms) |
| asis_ratio, heavy | 0.455, n=113 | 81 Saint Paul flips with ≥$30/sqft of permitted work between buy and resale (0.443) + 32 Minneapolis houses bought within 3 years of a VBR case (0.486) |
| asis_ratio, light | 0.747, n=308 | Saint Paul flips with permitted work under $30/sqft. Flips with no permit at all: 0.826, n=420 (not used) |
| asis_ratio, condemned | **unfilled** (0 pairs) | borrows heavy, and the row says so |
| asis_ratio by area | **Saint Paul + Minneapolis only** | suburban cities publish no permits with values here, so the two-city ratio is applied metro-wide |
| repair $/sqft | heavy $51 (n=81), light $4 (n=308) | Saint Paul `stp_permits` EST_VALUE_OF_WORK, all trades. Cross-check: the same flips resold for $140/sqft (heavy) and $56/sqft (light) more than they were bought for, so permits run 2-3x low. **Minneapolis permits carry no valuation** |
| vacant-sale second source | n=224, sale÷EMV 0.95 vs 1.02 for all Mpls sales | weak: the county already marks vacant houses down, so it understates the discount; reported, not used |

**Choice: worth as-is = ARV × asis_ratio, not ARV − repair cost.** The ratio is what buyers
actually paid, in 113 recorded transactions, and it carries holding cost, risk and margin.
Repair cost from permits is under-declared by a factor of 2-3 (the spread check above), so
subtracting it would overstate the as-is value. It is shown as a card, never subtracted.

## Timeline: the house's story (added 2026-10-11, `scripts/build_timeline.py` → `site/tl/*.json`)

| field | filled | source / note |
|---|---|---|
| county value by year | 7,258 of 7,262 | bricks `parcel_snapshot`: MetroGIS 2021-current (MKT_YEAR), Minneapolis assessor files 2017-2025. Hennepin leaves MKT_YEAR blank; MetroGIS release Y matches the Minneapolis assessor's year Y-1 value for value, so it is placed there. Realtor's `tax_history` is **not used**: its recent years are a taxable value that matches no county file |
| recorded sales | 19,218 | bricks `sale` (every deed any snapshot showed) + Realtor MLS "Sold" events, deduped within 200 days and 2%. A deed at the same price on the same day on other parcels is marked bulk and never read as a price (**outside Hennepin only**: Hennepin deed dates are months, so shared date+price is coincidence) |
| listing events | 50,105 | Realtor `property_history` (NorthstarMLS) |
| permits | 4,734 | Saint Paul all trades with value (to 2025-06-30); Minneapolis 2016- with no value |
| cases, vacant list, complaints | from `site/records.json` | complaints per event via `records.json` `ev` |

Every sentence in the story is a template in `site/index.html` (`events()`) over these
records; the current listing's sentence uses the same cut count and drop as the Cuts and
Dropped columns (counted from the last relist).
