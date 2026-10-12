# msp-house-index

**Live:** https://msp-house-index.vercel.app

> **2026-10-12:** the index now ranks by **room to negotiate**: the asking price minus what homes like it actually closed at (`SaleListModel`). The three estimates below are kept as evidence ("Records say it's worth"). Method and backtests: `DATA_MODEL.md`, "Valuation" and "Room to negotiate".

Every home for sale in the seven-county Minneapolis–Saint Paul metro, joined to
its county parcel, valued three independent ways, and ranked by how far the
asking price sits below what it's worth. The site shows the ten furthest below
value free; the full list is $100, once. Each row says why in one plain line,
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

**Block map.** Each opened row has a terminal-style diagram (plain SVG, in
brickandmortar.dev's terminal tones) of every lot within ~150 m, shaded by what the
county appraises it at, per finished sq ft or in total, in fifths of the homes in
frame. Shops, offices and apartments get a dashed line and the businesses on
them (Overture Places). `make map` rebuilds `site/map/` (~32 MB, 400 m cells, loaded
only when a row opens).

No number comes from a model. Every figure is a join or arithmetic on one.

## Run it

```bash
uv sync
make fetch     # ~45 min of polite Realtor.com requests, cached on disk per day
make county    # reads ~/Desktop/bricks/data/*.duckdb read-only
make build     # seconds: data/index.*, private/ (full set), site/ (top 10)
python scripts/qa_row.py 1 2 3   # everything behind a ranked row
VERCEL_TOKEN=… make deploy
```

Realtor.com endpoints are the ones its own pages call (`frontdoor/graphql`
`home_search` and `home`), found in `~/Desktop/house-lookup`. Requests are
spaced 1.5 s apart, 20 homes per detail request, everything cached.

## The paywall

`site/` ships only the ten homes furthest below value (the page's default sort) and
`site/meta.json` (the count and every city, for the offer). `make build` writes the
full set to `private/` (gitignored); `scripts/build_public.py` gates it, and
`scripts/deploy.py` uploads `private/bundle/` as `api/_full/`, which only the
`/api/data` function reads.

| route | does |
|---|---|
| `POST /api/checkout` | Stripe embedded Checkout, $100, Customer + Invoice; mints the buyer's password (`XXXX-XXXX-XXXX`: a nonce + an HMAC of it under `ACCESS_SECRET`) into the session metadata and the invoice footer |
| `POST /api/stripe-webhook` | `checkout.session.completed`: emails the buyer the password and a `/?pw=` link (Mailgun), once per session, and emails Aidan a sale notice |
| `GET /api/session-status` | the return page: paid or not, and the buyer's email; sends the password email if the webhook has not |
| `GET /api/data` | password in `x-password`: HMAC check, then a paid session for it in Stripe; returns every row, or `?id=` one home's records, `?cell=` one map cell |

Vercel env: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `ACCESS_SECRET`,
`MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, `MAILGUN_FROM`. A lost password:
`python scripts/fulfil.py <session_id>`; every sale: `--recent`; a free copy: `--comp`.

The git history before 2026-10-11 still holds the full index (`data/index.csv`,
`site/data.json`), because this repo is public.

## Output

`data/index.csv` / `data/index.parquet` (local, gitignored): one row per listing, ranked rows first,
with `rank`, `score`, `why`, the three estimates and every input behind them,
`exclusion` for the rows that weren't ranked, and the signals.

MIT.
