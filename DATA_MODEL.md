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

## Block map: what is the neighbourhood appraised at? (added 2026-10-11, written before probing)

Aidan: *"an aerial grid view of the neighborhood and what they are appraised at… helps visualize
the neighborhood valuation dynamic."* The ideal record, from the need alone:

### MapParcel — *one lot near the listing*

| field | question it answers |
|---|---|
| lot line (polygon) | where does this lot sit, on the photo |
| emv_total | what does the county say the whole lot is worth |
| finished sq ft | how big is the house, so value reads per square foot and a big house isn't just "valuable" |
| emv ÷ finished sq ft | is this house appraised high or low *for its size*, against its neighbours |
| year built | is it the same vintage as the listing |
| last sale date + price | what a buyer actually paid, and when |
| address (number + street) | which house is this, to go and look |
| units on the lot (stacked condo parcels) | is this one house or a building of units |
| is_listing | which lot is the one for sale |

### Neighbourhood — *the frame around one listing*

| field | question |
|---|---|
| radius (~150-200 m) | the block and the blocks facing it |
| value breaks (quantiles of the parcels in frame) | what counts as high or low *here* |
| aerial photo, recent, leaf-off | what is physically there: garages, additions, vacant lots, the park |

## Neighborhood: what is around this house? (added 2026-10-11, written before probing)

Aidan: *"any other data from brickandmortar.dev that would be interesting and applicable?"*
Applicable means: a fact someone evaluating this one home would act on. Each is a fact
about the parcel or a measured count around it, carrying its source and date.

### ParcelRules — *what can I do with this lot?*

| field | question it answers |
|---|---|
| zoning_district (code + name) | what the city lets you build here |
| zoning_purpose (the code's own sentence, section cited) | what that district is for, in the city's words |
| historic (landmark / historic site, on or beside the lot) | will a rehab or exterior change need preservation review |
| lead_service_line (city classification) | will the water line need replacing |

### Hazards — *what is in the ground nearby?*

| field | question |
|---|---|
| cleanup sites within 200 m (name, program, open/closed, distance) | is there known contamination next door |

### Investment — *is money going into this area or leaving it?*

| field | question |
|---|---|
| permits within 400 m, last 2 years (count, declared value) | are neighbours fixing up |
| new dwellings approved within 400 m | is anyone building here |
| demolitions within 400 m | are houses coming down |
| city median of each, same radius and window | is that a lot or a little *here* |

### Block — *who owns the street?*

| field | question |
|---|---|
| homes on the block, and how many send the tax bill elsewhere | owner-occupied street, or a landlord street |

### Access — *how do I get around, and how loud is it?*

| field | question |
|---|---|
| transit stops within 400 m, nearest distance | can I live here without a car |
| busiest counted road within 100 m and its daily traffic | is the house on a loud road |

### Left out on purpose

Crime, shots fired, 911 calls, police stops and census demographics. Shown next to homes for
sale they steer buyers by who lives in a neighbourhood, which is a fair-housing problem and the
reason the big listing sites took crime maps down. They are in the bricks substrate; they are
not joined here. FEMA flood zone is already on the record list (`build_records.py`).

### Reality check for the block map (2026-10-11, `scripts/build_map.py` → `site/map/<cell>.json`)

| field | filled | source / note |
|---|---|---|
| lot lines | 397,607 lots around 7,262 of 7,262 ranked listings | MetroGIS parcels, all 7 counties, simplified to 1 m. Dakota draws 18,882 condo/townhome units as points with no lot polygon: drawn as dots |
| emv_total | every lot the county values | MetroGIS current |
| finished sq ft | county file outside Hennepin (~80-95% of dwellings); Minneapolis from the city assessor's 2025 file (115,771 parcels) | **Hennepin outside Minneapolis: unfilled.** No public file carries it; those maps open on total value and say why |
| stacked units | median *dwelling* unit per lot | parking/storage stall parcels ($9k) would otherwise drag a condo tower's median down |
| year built, last sale | county file | Hennepin sale dates are months |
| not a home | 34,100 lots | county `is_dwelling` false (commercial, industrial, apartments 4+, churches, parks); drawn with a dashed line |
| businesses on them | 22,677 | **Overture Places** (CDLA-Permissive-2.0), confidence ≥ 0.5, point in a non-home lot only, so a home-based registration never names a house. **Google ratings/status not shown**: bricks' RIGHTS_WITHHELD class, and a static file here is redistribution. **MN SOS bulk not used**: non-commercial licence |
| aerial photo | 7-county, spring 2025, leaf-off, 30 cm | Metropolitan Council via MnGeo WMS (`met25`), **public domain** (Minn. Stat. ch. 13, per its metadata); attributed under the map |
| ~~aerial photo~~ | dropped 2026-10-11 | Aidan chose a terminal diagram over the photo the same evening ("diagrams in plain terminal tones make navigating and understanding easier"). Street names are placed along the line of lots that share one, since the parcel file has no roads |

Payload: 32.0 MB in 9,553 cell files of 400 m; an opened row fetches 1-4 of them (median 9 KB).

### Reality check for neighborhood (2026-10-11, `scripts/build_neighborhood.py` → `private/nb/<property_id>.json`)

Of 7,262 ranked listings; 6,987 get a file. Every layer is `open-public-record` in bricks
`layer.licence`; the build asserts it.

| field | filled | source / note |
|---|---|---|
| zoning district + code sentence | 1,777 (every Mpls + StP listing but 4) | the cities' own district polygons (Mpls land-use districts, StP zoning). Purpose quoted verbatim, section cited: Mpls Title 20 §530 (July 2023 edition), StP §66.212-213 (Ord 23-43). Other districts: code + link. **Suburbs unfilled**: bricks `zoning_district` is the county's property-use class ("RESIDENTIAL", "1A/1B/4BB"), not zoning, so it is not used |
| historic | 27 (25 the lot or building itself) | Mpls HPC landmarks, StP historic sites, by PIN or within 40 m. **No historic *districts*** in either layer |
| cleanup sites ≤200 m | 1,805 | MPCA WIMN, cleanup activities only (leak sites, brownfields, Superfund, site assessment…); tanks, waste generators, stormwater and air permits dropped. **Status is the site-level `active_flag`** (80% of sites read active); MPCA's per-activity API, which says which leak site is closed, bot-walls after a burst, so lines say "MPCA file active" and link the site page. Amber = nearest within 100 m |
| lead service line | 703 (Mpls houses and multis ~100%; condos 10 of 415) | Mpls Service Line Inventory, classification decoded by the service's own coded-value domain (1 Lead, 2 Non-Lead, 3 Unknown, 4 GRR). **Saint Paul's inventory is not in bricks** |
| permits ≤400 m, 2 yr, vs city median | 1,777 | Mpls `Res`+`Commercial` permits Sep 2024–Sep 2026 (count only, no value); StP `Building Permit` Jul 2023–Jun 2025 with declared value. Median over 2,500 sampled homes per city: Mpls 117, StP 101. **Suburbs unfilled** |
| new dwellings, demolitions ≤400 m | 773 / 643 (shown only when >0; city median is 0 for both) | Mpls `dwellingUnitsNew` and `Wrecking` permits; StP new-dwellings and demolition layers |
| block owned from elsewhere | 6,858 | not homesteaded AND tax bill to another address (each alone misfires: escrow servicers and PO boxes on homesteads; builders' spec homes). Dakota publishes no mailing address: homestead only, said on the line. Names and addresses never leave the script. Median block 8% |
| transit ≤400 m | 3,045 | Metro Transit GTFS 2026-08-29 stops |
| busiest road ≤100 m | 721 | MnDOT AADT, latest year per count location. The point is the counted segment's midpoint, so this under-finds roads; stated on the line |

Payload: 8.6 MB across 6,987 files, median 461 B, max 4.9 KB; one file is fetched per opened row.

## Gated per-listing data (added 2026-10-11, the $100 paywall)

**Free:** `site/` holds the 10 homes furthest below value (the page's default sort,
`under()` in `site/index.html`, mirrored in `scripts/build_public.py`) and every file
those 10 need. **Paid:** everything else, read only by `site/api/data.js`.

| where | what |
|---|---|
| `private/` (gitignored) | the full set every build step writes: `data.json`, `evidence.json`, `records.json`, `tl/`, `map/`, and any new layer |
| `scripts/build_public.py` (last step of `make build` and `make map`) | copies the top 10's slice into `site/` and packs the rest into `private/bundle/` |
| `private/bundle/` → `api/_full/` (by `scripts/deploy.py`) | `rows.json.gz` (all rows), `detail.json.gz` (`{pid: {ev, rec, tl, <layer>…}}`), `map/<0-63>.json.gz` (cells), `passes.json` (comp labels) |
| `GET /api/data` + `x-password` | all rows; `?id=<pid>` one home's detail; `?cell=<x_y>` one map cell |

**Adding a per-listing layer** (neighbourhood is next): write it to `private/` only, either
`private/<name>.json` keyed by property_id (add `<name>` to `PER_LISTING`) or
`private/<name>/<property_id>.json` (add to `PER_LISTING_DIRS`). On the page, read it with
`layer("<name>", property_id)`: the public file for the top 10, the gated route once unlocked,
`null` when the home has none. Never write a layer into `site/` directly: it would ship every
listing free.

**Passwords** are stateless, `XXXX-XXXX-XXXX` = a 5-character nonce + 7 of HMAC-SHA256 under
`ACCESS_SECRET`. Two kinds: *paid* (minted at checkout, valid once Stripe shows its session paid)
and *comp* (`scripts/issue_password.py "<label>"`, valid with no session; label logged on unlock).
The public repo's history before this date still holds the full set.

## Valuation: comps an appraiser would sign (added 2026-10-12)

Aidan: *"quoting what we think the property is worth is the thing that is going to get the most scrutiny… It needs to IMPRESS them."* Test case: 8031 Tierneys Woods Curv, Bloomington, asking $499,900, which the index called worth $921k.

### Method (`msp/valuation.py`, scored by `scripts/backtest.py`)

- **Data.** 44,919 closed MLS sales Oct 2025–Oct 2026 (`sold_raw.jsonl`), each enriched by `scripts/fetch_comp_details.py` (Realtor detail, cached): garage stalls, basement and finish, walkout, foundation area, full/half baths, roof age, the listing's words, the parcel number (→ county EMV) and the home's sale history (→ quick resales).
- **Comp choice.** Same kind of home, sold before the valuation date, ranked by a similarity score (distance, months ago, size, age, beds, baths, lot, measured condition, same subdivision or building). Best 6.
- **Time.** Each sale brought to the valuation month with the area's monthly index: the month terms of a log-price regression on that area's own sales.
- **Adjustments.** One log-price regression per area (Minneapolis, Saint Paul, each county) × house/attached, with ZIP and month terms. A coefficient with the wrong sign (a bedroom worth less at equal size) is dropped and refit, never applied. Every adjustment line on the page names its rate and how many sales measured it. Condition uses the measured phrase effects below.
- **Reconciliation.** Weighted mean of the adjusted prices, weight ∝ 1/(0.5+similarity)/(0.05+gross adjustment). Blended 50/50 with a second reading of the same comps: their sale ÷ county EMV, applied to the subject's EMV (a comp sold at under 0.5× or over 2× its EMV is a mis-matched parcel and skipped). The blend won on the validate window (comp median error 8.6% → 7.8%).
- **Tuned** on the 90 days before the holdout (validate window); the holdout was scored once.

### Backtest: 11,886 homes sold 2026-07-14 onward, each valued as of the day before it sold

| method | n | median abs error | within 5% | within 10% | within 20% | bias |
|---|---|---|---|---|---|---|
| comps as shipped (median $/sqft of 10 nearest) | 11,422 | 9.5% | 29.4% | 51.9% | 78.7% | +0.1% |
| adjusted grid | 11,885 | 8.6% | 32.3% | 55.9% | 80.4% | +0.8% |
| same comps × county EMV | 9,509 | 7.8% | 35.1% | 59.7% | 83.9% | +1.1% |
| comp value (grid ½ + EMV ½) | 11,885 | 7.6% | 36.0% | 60.6% | 83.0% | +0.9% |
| worth as shipped (middle of comps, county, last deed) | 11,728 | 7.4% | 36.1% | 61.1% | 83.8% | -1.0% |
| worth, new comp value | 11,885 | 7.2% | 37.1% | 62.3% | 83.1% | -0.7% |
| expected sale given the ask | 11,885 | 2.4% | 74.6% | 88.9% | 94.6% | +1.1% |

**Homes asking 25%+ under the shipped worth** (192 in the holdout): shipped worth was off by a median 48.4% (bias +48.4%), new worth 38.3%, expected sale given the ask 3.2%. **A deep discount against our records is almost always something the records can't see.**

### The ask is information (`AskModel`, `data/ask_model.json`, fit on 20,963 closed sales)

| ask vs our estimate | n | sale vs estimate (fit) | sale vs estimate (observed median) | sale vs ask (fit) |
|---|---|---|---|---|
| -39% | 80 | -41.2% | -46.5% | -3.0% |
| -33% | 87 | -34.0% | -32.2% | -1.5% |
| -26% | 212 | -25.9% | -25.1% | -0.0% |
| -18% | 809 | -17.7% | -16.7% | +0.5% |
| -10% | 3,969 | -8.9% | -7.6% | +0.7% |
| +0% | 8,812 | +0.7% | +0.3% | +0.7% |
| +10% | 4,477 | +10.0% | +8.8% | -0.5% |
| +22% | 2,945 | +22.1% | +30.4% | -0.1% |

In every band homes sold within ~3% of their ask. Used as the value wherever the ask is 25%+ under our records value (`deep_gap`): on the holdout those homes sold a median 38.3% under our records value and 3.2% from the expected sale. Where the gap is 30%+ and nothing on record or in the listing explains it, the row also carries `unexplained_gap` (amber). Where the listing's words measure as needs-work, County and Last sale take the same measured discount the comps do (they never see inside).

### Condition words, measured (`scripts/mine_condition.py`, 21,041 sales before the holdout)

Effect = median sale ÷ plain comp value of sales whose listing used the words, against all sales.

| group | n | effect | t |
|---|---|---|---|
| as-is sale | 173 | -20.7% | -12.9 |
| needs work | 267 | -20.2% | -13.9 |
| investor / cash | 286 | -18.9% | -12.7 |
| renovation euphemism | 64 | -14.1% | -5.0 |
| build / land | 91 | -7.9% | -4.5 |
| cosmetic / personal touches | 732 | -6.5% | -11.5 |
| bones / potential | 691 | -6.2% | -9.3 |
| original / dated | 33 | +2.7% | -0.6 |
| renovated | 4230 | +3.3% | 14.8 |

Groups with |t| < 2.5 are not used. Single phrases and the mined 2-4 word phrases are in the JSON.

### Adjustment rates (`data/valuation_model.json`, $ at each area's median price)

| area | n | size +10% | full bath | half bath | garage stall | lot +25% | dropped (wrong sign) |
|---|---|---|---|---|---|---|---|
| Minneapolis | 3,304 | $21,000 | $25,900 | $37,600 | $4,000 | $10,900 | beds |
| Hennepin County | 8,845 | $26,400 | $23,500 | $25,200 | $15,400 | $13,900 | beds, bsmt |
| Saint Paul | 2,672 | $15,100 | $28,500 | $26,400 | $8,900 | $11,600 | below_share |
| Ramsey County | 2,130 | $20,700 | $24,900 | $30,900 | $8,000 | $10,300 | beds, bsmt |
| Anoka County | 3,978 | $17,600 | $8,500 | $14,400 | $15,800 | $7,700 | beds, bsmt |
| Dakota County | 4,446 | $25,100 | $9,200 | $11,500 | $8,900 | $11,700 | beds, bsmt |
| Washington County | 3,383 | $31,500 | $200 | $8,900 | $20,400 | $9,700 | beds, bsmt |
| Scott County | 1,796 | $23,700 | $21,900 | $22,100 | $19,800 | $15,900 | beds, bsmt, roof_new |
| Carver County | 1,523 | $35,800 | $10,400 | $8,200 | $15,000 | $19,400 | beds, bsmt |
| Twin Cities metro | 32,077 | $23,700 | $18,600 | $23,200 | $9,600 | $11,800 | beds, bsmt |

### What's still weak

- Under $200k and over $700k: median error 15% and 11% (records-only). Few comps, more variety.
- Interiors: the county never sees inside, and listing words catch only what the agent admits.
- MLS sqft includes finished basements; above-grade sqft is inferred from the foundation area (`below_share`), not read from the county (Hennepin publishes none).
- Block median now needs 3+ sales (`build_block.py`).
- Basement terms (`bsmt`, `below_share`; `bsmt_fin` was removed because it double-counted `below_share`) came out with the wrong sign in most suburban areas and are dropped there, so the Tierneys-style "5,400 sqft includes a basement" correction only applies in Minneapolis and Saint Paul. Bedrooms are dropped everywhere: at equal size, more bedrooms do not sell for more.

## Room to negotiate: what it will really sell for (added 2026-10-12)

Aidan's decision, after the curve above showed closed sales land within ~3% of the ask whatever the records say: **rank by room to negotiate** = ask − predicted sale. The records value stays as evidence.

### Ideal model, then reality

| field | question | filled from |
|---|---|---|
| predicted sale ÷ ask | what will it really close at | `SaleListModel` (`msp/valuation.py`) |
| days listed | is the seller tiring | listing date (search); for closed sales, listed → close minus a 35-day closing lag |
| price cuts (count, share of first ask), relisted | is the seller already moving | Realtor `property_history`, this listing run only |
| ask ÷ records value | is it priced under or over the evidence | the condition-aware worth above |
| area, price band, type, season | how hot is this market | city/county; ask band; house/townhome/condo; quarter |
| condition words, HOA | what the buyer will discount | measured phrase groups; HOA ≥ $300/mo |
| **showings, offers, days to pending** | **real demand** | **unfilled: no public source. Days are listing → close minus a fixed lag** |

### Backtest (time split): fit on 11,205 sales 2025-12-01 → 2026-07-14, scored on 10,151 sold since

| | median error vs actual sale | within 2% | within 5% | within 10% | bias |
|---|---|---|---|---|---|
| predicted sale | 2.19% | 46.4% | 84.0% | 96.4% | +0.73% |
| the ask alone | 1.79% | 53.6% | 83.9% | 96.2% | +0.0% |
| long-listed or 2+ cuts (1,248), predicted | 2.2% | | 80.2% | | +0.25% |
| same, the ask alone | 1.64% | | 81.2% | | +0.84% |

**Read this plainly:** as a point estimate the prediction is no closer than the ask itself (the market cooled through the holdout and the level ran ~0.7% high). What it adds is order: sorted into tenths by predicted room, the homes really did sell from under to over their ask.

| tenth (most room first) | n | predicted sale vs ask | actual sale vs ask |
|---|---|---|---|
| 1 | 1,015 | -3.6% | -2.2% |
| 2 | 1,015 | -1.8% | -1.8% |
| 3 | 1,015 | -0.9% | -1.0% |
| 4 | 1,015 | -0.1% | -0.9% |
| 5 | 1,015 | +0.5% | -0.3% |
| 6 | 1,015 | +1.1% | -0.1% |
| 7 | 1,015 | +1.6% | +0.5% |
| 8 | 1,015 | +2.2% | +0.8% |
| 9 | 1,015 | +2.8% | +1.3% |
| 10 | 1,016 | +4.0% | +3.4% |

### Coefficients (`data/sale_list_model.json`, refit on all 21,142; market-temperature shift from the last 60 days +0.0002)

| input | effect on sale ÷ ask |
|---|---|
| ask vs records value (log, ±0.3) | -4.0% |
| no records value | -0.0% |
| price cuts so far (share of first ask) | -11.7% |
| relisted | -0.6% |
| needs-work words | -0.2% |
| renovated words | +0.8% |
| HOA $300+/mo | +0.1% |
| listed 2-6 weeks | -1.1% |
| listed 6-13 weeks | -1.3% |
| listed 3+ months | -0.5% |
| 1 price cut | -1.2% |
| 2+ price cuts | -0.4% |
| ask under $200k | -0.4% |
| ask $200-350k | +0.3% |
| ask $500-750k | -0.4% |
| ask $750k+ | -1.6% |
| townhome | -1.5% |
| condo | -3.2% |
| Saint Paul | -0.1% |
| Hennepin County | -0.4% |
| Ramsey County | -0.2% |
| Anoka County | -0.4% |
| Dakota County | -0.4% |
| Washington County | -0.4% |
| Scott County | -0.7% |
| Carver County | -0.4% |
| spring (Apr-Jun) | -0.2% |
| summer (Jul-Sep) | -1.0% |
| fall (Oct-Dec) | -0.1% |

A ±0.3 clip on the ask-vs-records gap and one '3+ months' bucket keep thin cells from driving the price; a 6+ month bucket alone predicted a +5% premium. A recalibration fitted on the validate window made the holdout worse (bias +0.9%) and is off (`NO_CAL` in `scripts/backtest.py`).
