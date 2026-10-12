# msp-house-index

**Live:** https://msp-house-index.vercel.app

Every home for sale in the seven-county Minneapolis–Saint Paul metro, joined to
its county parcel, valued three independent ways, and ranked by how far the
asking price sits below what it's worth. Each row says why in one plain line,
and expands to the arithmetic behind it.

> asking $150k; 10 similar homes nearby sold for ~$246k; EMV $268k ×1.14 local
> ratio = $306k; last sold $228k in 2022 ≈ $243k today; 2 price cuts

## Why not just asking price minus the county's value

Assessors lag the market, and the lag isn't even. In Hennepin, homes in the
cheapest fifth sold for 1.04× their assessed value in 2025–26 and the most
expensive fifth for 1.10× (`scripts/build_county.py` prints this). A raw
ask-minus-EMV ranking sorts by assessment error. So each listing gets three
opinions:

| opinion | how | source |
|---|---|---|
| **County, corrected** | EMV × median sale÷EMV of the 30 nearest arm's-length recorded sales whose EMV is within ±35% of this one (≤5 mi) | MetroGIS parcels + county deeds; Hennepin's own arm's-length code |
| **Comparable sales** | median $/sqft of up to 10 closed sales in the last year, same kind of home, ±25% size, built within 25 years, ≤2 mi × this home's sqft | Realtor.com closed listings (MLS) |
| **Last sale, brought forward** | last deed (if 2+ years old) × that county's price change since, measured as sale÷today's EMV by sale year | county deeds |

**Score** = mean discount of ask below the three opinions (a missing opinion
counts as 0, out of 3) − ½ × their standard deviation. Three opinions agreeing
beat one big gap. Realtor.com's own AVMs would be a fourth; it publishes none for
these Minnesota listings (see `DATA_MODEL.md`).

**Not ranked**, each with its reason in the CSV: under contract, lots and land,
new builds the assessment hasn't caught, teardowns, condo units that couldn't be
matched to their own parcel, co-ops, multi-parcel listings, auctions, mobile
homes, and listings whose EMV is 3× off the ask (a wrong parcel, not a bargain).

**Signals** beside the score: days listed, price cuts, permits pulled after the
assessment date (finished = upside the EMV hasn't seen, open = risk; Minneapolis
live, Saint Paul through 2025-06-30), Minneapolis vacant-building and nuisance
cases, Saint Paul's vacant building list, the deed-theft exposure list, and
distress words in the listing text.

**Condition.** All three opinions are after-repair values. A home the records or
its listing say needs work (vacant lists, vacant-building cases, unfixed housing
violations, "major rehab", "fire damage"…) gets a tier, and its value becomes
**worth as-is = middle estimate × the as-is ratio**, measured from recorded flips:
the same house bought and resold within 2 years (`scripts/build_condition.py`,
`data/condition.json`, method in `DATA_MODEL.md`).

No number comes from a model. Every figure is a join or arithmetic on one.

## Run it

```bash
uv sync
make fetch     # ~45 min of polite Realtor.com requests, cached on disk per day
make county    # reads ~/Desktop/bricks/data/*.duckdb read-only
make build     # seconds: data/index.parquet, data/index.csv, site/data.json
python scripts/qa_row.py 1 2 3   # everything behind a ranked row
VERCEL_TOKEN=… make deploy
```

Realtor.com endpoints are the ones its own pages call (`frontdoor/graphql`
`home_search` and `home`), found in `~/Desktop/house-lookup`. Requests are
spaced 1.5 s apart, 20 homes per detail request, everything cached.

## Output

`data/index.csv` / `data/index.parquet`: one row per listing, ranked rows first,
with `rank`, `score`, `why`, the three estimates and every input behind them,
`exclusion` for the rows that weren't ranked, and the signals.

MIT.
