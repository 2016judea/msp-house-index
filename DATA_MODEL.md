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
