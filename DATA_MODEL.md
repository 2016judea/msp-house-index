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
