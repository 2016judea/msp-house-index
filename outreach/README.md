# Outreach — asking for feedback on the index

Nothing here has been sent. Every message is a draft for Aidan to read first.

`ledger.csv` and `drafts/` stay on this machine. They hold people's addresses, and this repo is public, so both are in `.gitignore`.

## Channel

**Email that opens on the reader's own public record.** That means their article, their meetup or workshop page, their podcast episode, their loan terms, or a house they bought and listed. The message asks for nothing except "what's wrong with it". Of every message that ever got a yes, all eight opened this way, and the ones that opened on us got silence (`distribution-channel` skill, read 2026-10-05).

**Super users come first.** Investor-meetup hosts, investor-friendly agents, lenders and homebuyer counsellors each stand in front of many buyers, so one reply from them reaches dozens. Reporters reach the most people of anyone. Flippers are the direct users.

Reddit and Facebook are replies to specific threads and group posts. They are not counted toward the 50-recipient floor.

## Sample (BUSINESS.md rule 4: 50+ recipients across 3+ segments)

| segment | email | other |
|---|---|---|
| reporter | 14 | |
| investor-hub (meetups, podcasts, investor agents) | 14 | 1 Meetup message |
| flipper / rehab investor | 12 | 1 contact form |
| lender (hard money, private) | 7 | 1 contact form |
| counsellor (first-time-buyer nonprofits) | 12 | |
| community (Reddit, Facebook groups) | | 7 Reddit + 2 Facebook |

That makes 59 emails across 5 segments. Each address was found published on the person's own page or their organisation's, and none were guessed from a pattern. The URL where each one was seen is in the ledger.

Log `sent_on` and `reply_kind` in the ledger as touches go out. Silence counts as a finding only after three touches (`distribution-channel` rule 5).

## v2: what changed (2026-10-11)

The product changed under the v1 drafts: homes that need work are now valued as-is (buy÷resale from 421 recorded flips), each home opens to its value cards, timeline, violations, block map and neighbourhood facts, and the full directory is $100 behind a password. The v1 hooks were ranks from before rehab pricing, so several stopped being true. Example: the two Frogtown houses on Sherburne were #1 and #2 and now rank around #1,400 and #1,900.

**The offer.** Each recipient gets their own free password to the full directory, plus one line asking what's wrong and what's missing. Price talk stops at "it's normally $100".

**Drafts come from a script.** `python outreach/render_drafts.py` reads `ledger.csv` and the full index in `private/`, and writes every draft again. Each draft is four parts: the reader's own record (the hand-written `opener` column), one sentence on the index, a hook paragraph computed from the data for that reader's area or property (`focus` column), and the password plus the ask. Re-run it after any rebuild, including the valuation upgrade, and every number follows the data. The `hook_check` column records what each hook found, and flags any home that is no longer ranked. The old drafts are in `drafts/_v1/`.

**Passwords (`make_passwords`).** `scripts/issue_password.py` doesn't exist yet, so the drafts carry `{{PASSWORD}}`. Each ledger row has a `password_label` (`comp:<name-slug>`). Once the script lands, run `python outreach/render_drafts.py --issue`. It calls the script once per label, expects the password on the last line of stdout, and caches it in `drafts/.passwords.json`, so a re-render never mints a second password.

**Community rules, re-checked.** These replies go in existing threads and are never new posts.
- r/TwinCities and r/saintpaul (no commerce or advertising): a link to a paid tool is commerce now. The reply answers the question with no link, and the password goes by DM only if they ask. The DM text sits under each draft.
- r/FirstTimeHomeBuyer (no self-promotion): the reply answers from the records and never names the tool.
- r/realestateinvesting and r/RealEstate: still excluded.
- Facebook groups: join and read the rules first, and skip the group if promo posts are barred.

**Time-sensitive** (the `time_sensitive` column):
- The r/TwinCities market-temperature thread archives about Oct 14. Reply by Oct 13 or drop it.
- The counsellor openers name the Oct 24–25 Home Stretch workshops. Send by about Oct 20, or reword them to the past tense.
- The MnREIA opener names the Oct 19 happy hour.
- The MnREIA Facebook post names the Nov 3 meeting.

## v3: room to negotiate (2026-10-12)

The index now ranks by **room to negotiate**: for each listing it predicts the sale price from how recent closed sales landed against their ask, and ranks by ask minus that prediction. Sales land within a few percent of ask whatever the records say, so "worth $X, N% under value" is gone from every draft. The records value shows up only as evidence ("Records say it's worth about…").

- **Hooks** state how a home is likely to sell against its ask: "150 days listed, 4 cuts, likely to sell 4% under its $120k ask". When the best home in an area sells at or over ask, `hook_check` flags it as `NO ROOM`.
- **The tested claim** is read from `data/backtest.json` (`sale_list.deciles`) and goes to reporters, lenders and investor hosts. It says that, of homes sold since July, the tenth ranked with the most room sold under ask, and the tenth with the least sold over.
- **Needs-work hooks** no longer use the as-is ratios. They name the needs-work home with the most room, plus the record that marks it.
- **Passwords are minted.** `render_drafts.py --issue` calls `scripts/issue_password.py` per `password_label` and caches the results in `drafts/.passwords.json`. Each draft carries the password and a `/?pw=` link. A label gets logged in `private/comp_passwords.csv` when it's issued, and appears in unlock logs only after the next deploy ships `passes.json`.
- **Archives:** v2 drafts are in `drafts/_v2/` and v1 drafts in `drafts/_v1/`.
