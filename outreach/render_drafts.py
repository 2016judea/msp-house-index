"""Render the outreach drafts from the ledger and the current index.

    python outreach/render_drafts.py            # write outreach/drafts/*.md, update ledger hook columns
    python outreach/render_drafts.py --issue    # also mint a password per recipient (needs scripts/issue_password.py)

Nothing here sends anything. Every draft is for Aidan to read first.

Inputs (all local, all gitignored except this script):
  outreach/ledger.csv     one row per recipient. Columns this script reads:
                          segment, channel, address_or_handle, hook_url, draft_file,
                          greeting, subject_v2, opener (the reader's OWN public record, written by hand),
                          focus (what slice of the index to describe, see FOCUS below), password_label
  private/data.json       the full ranked set (make build)
  private/tl/NN.json      per-home timelines (sales, assessments, list history)

Every number in a hook is computed here from private/, never typed. Re-run after
any rebuild of the index and the drafts follow the data.

FOCUS is `key=value` pairs joined by `;`:
  kind=area|own|flips|metro|cuts|market
  zip=55411,55412  city=Saint Paul,Minneapolis  county=hennepin  type=house|multi|condo|townhome
  maxask=300000  tier=light,heavy,condemned  signal=foreclosure  addr=<street address> (kind=own)
  three=1 (only homes with all three value opinions)  label=<how the slice is named in the email>
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outreach"
LEDGER = OUT / "ledger.csv"
DRAFTS = OUT / "drafts"
PWCACHE = DRAFTS / ".passwords.json"  # drafts/ is gitignored
SITE = "https://msp-house-index.vercel.app"
PRICE = 100
WORD_LIMIT = 120
RANKED = True  # False for replies where the sub bars self-promotion: no rank, no tool

# Subreddit rules, re-read 2026-10-11 from the ledger notes.
#   nolink: rule bars commerce/advertising; a paid tool link is commerce now there is a paywall.
#           Reply answers the question; the password goes by DM only if they ask.
#   notool: rule bars self-promotion; the reply answers from the records and never names the tool.
SUB_RULES = {"twincities": "nolink", "saintpaul": "nolink", "firsttimehomebuyer": "notool"}
# Never post in r/realestateinvesting or r/RealEstate (both ban it outright; kept from v1).
EXCLUDED_SUBS = {"realestateinvesting", "realestate"}

ASK = {
    "reporter": "What's wrong in it, and what's missing before you'd trust a number from it?",
    "investor-hub": "What's wrong with it, and what's missing for the investors you work with?",
    "flipper": "What's wrong with it, and what's missing before you'd buy off it?",
    "lender": "Where do my values miss yours? And what's missing?",
    "counsellor": "What's wrong with it, and what would your buyers need from it?",
    "community": "If something in it looks wrong to an investor, tell me. Same for what's missing.",
}

# ---------------------------------------------------------------- data


def load():
    d = json.loads((ROOT / "private/data.json").read_text())
    rows = sorted(d["rows"], key=lambda r: r["rank"])
    tl = {}
    for f in (ROOT / "private/tl").glob("*.json"):
        tl.update(json.loads(f.read_text()))
    return d, rows, tl


def money(v):
    if v is None:
        return "?"
    if v >= 995_000:
        return f"${v / 1e6:.2f}M"
    return f"${round(v / 1000):,}k"


def pct(x):
    return f"{round(abs(x) * 100)}%"


def below(r):
    """Share the ask sits below value (negative = above)."""
    return 1 - r["ask"] / r["worth"] if r.get("worth") else 0


def parse_focus(s):
    f = {}
    for part in filter(None, (p.strip() for p in s.split(";"))):
        k, _, v = part.partition("=")
        f[k.strip()] = v.strip()
    return f


def select(rows, f):
    def ok(r):
        if "zip" in f and r["zip"] not in f["zip"].split(","):
            return False
        if "city" in f and r["city"] not in f["city"].split(","):
            return False
        if "county" in f and r["county"] not in f["county"].split(","):
            return False
        if "type" in f and r["type_group"] not in f["type"].split(","):
            return False
        if "maxask" in f and r["ask"] > int(f["maxask"]):
            return False
        if "tier" in f and r.get("tier") not in f["tier"].split(","):
            return False
        if "signal" in f and f["signal"] not in (r.get("signals") or []):
            return False
        if f.get("three") and r.get("n_estimates", 0) < 3:
            return False
        return True

    return [r for r in rows if ok(r)]


def why_asis(r):
    """Plain reason a home is valued as-is, from its strongest tier fact."""
    facts = r.get("tier_facts") or []
    for x in facts:
        if x.startswith("Saint Paul vacant building list"):
            return "it's on St. Paul's vacant building list"
        if x.startswith("Minneapolis vacant building case"):
            return "Minneapolis has a vacant-building case open on it"
        m = re.match(r"(\d+) Minneapolis housing violations", x)
        if m:
            return f"Minneapolis cited {m.group(1)} housing violations, not fixed"
    for x in facts:
        m = re.match(r'Listing says "(.+)"', x)
        if m:
            return f'the listing says "{m.group(1)}"'
    return "the records say it needs work"


def home_line(r, n_total, city=True):
    where = f"{r['address']}, {r['city'].replace('Saint ', 'St. ')}" if city else r["address"]
    rank = f", #{r['rank']:,}" if RANKED else ""
    s = f"{where}{rank}: asking {money(r['ask'])}, worth about {money(r['worth'])}"
    if r.get("tier") not in (None, "none"):
        s += f" as-is, {money(r['arv'])} fixed up; {why_asis(r)}."
    else:
        s += "."
        if (r.get("block_n") or 0) >= 3 and r.get("block_med"):
            s += f" Its block's median sale is {money(r['block_med'])}."
    return s


def last_sale(tl, r):
    t = tl.get(r["property_id"]) or {}
    sales = sorted(t.get("sales") or [], key=lambda s: s["d"])
    return sales[-1] if sales else None


# ---------------------------------------------------------------- hooks


def hook(f, rows, d, tl):
    """Return (hook_text, check_note). check_note says what survived the data check."""
    n = len(rows)
    kind = f.get("kind", "area")
    label = f.get("label", "the metro")
    cond = d["condition"]["tiers"]
    n_flips = cond["light"]["n"] + cond["heavy"]["n"]

    if kind == "market":
        cut = sum(1 for r in rows if (r.get("n_cuts") or 0) > 0)
        old = sum(1 for r in rows if (r.get("dom") or 0) >= 90)
        k20 = sum(1 for r in rows if below(r) >= 0.2)
        return (f"Of the {n:,} homes for sale I could value across the seven-county metro, {cut:,} ({pct(cut / n)}) "
                f"have cut their price at least once. {old:,} have sat 90 days or more. "
                f"And {k20:,} ask at least 20% under what nearby sales and the county say they're worth.",
                f"market counts recomputed over {n:,} ranked")

    if kind == "metro":
        sub = select(rows, f)
        k20 = sum(1 for r in sub if below(r) >= 0.2)
        top = sub[0]
        if f.get("three"):
            return (f"{len(sub):,} of them carry all three opinions: county assessment, recent comparable sales, "
                    f"and the last sale brought forward. #1 of those is {top['address']}, {top['city'].replace('Saint ', 'St. ')}: "
                    f"asking {money(top['ask'])}, comps put it at {money(top['est_comp'])}.",
                    f"three-opinion top recomputed: {top['address']} #{top['rank']}")
        return (f"{k20:,} ask at least 20% under value. #1: {home_line(top, n)}",
                f"metro top recomputed: {top['address']} #{top['rank']}")

    if kind == "cuts":
        sub = [r for r in rows if (r.get("n_cuts") or 0) >= 3]
        return (f"{len(sub):,} have cut their price three times or more. Top of those: {home_line(sub[0], n)}",
                f"3+ cuts top: {sub[0]['address']} #{sub[0]['rank']}")

    if kind == "flips":
        sub = select(rows, f)
        top = sub[0]
        return (f"Homes that need work are valued as-is, from {n_flips} recorded metro flips: heavy rehabs bought at "
                f"{pct(cond['heavy']['ratio'])} of resale, light at {pct(cond['light']['ratio'])}. "
                f"Top of the {len(sub):,} {label}: {home_line(top, n)}",
                f"flip ratios + needs-work top: {top['address']} #{top['rank']}")

    if kind == "own":
        a = f["addr"].lower()
        mine = [r for r in rows if r["address"].lower().startswith(a)]
        area_f = {k: v for k, v in f.items() if k not in ("addr", "kind")}
        area = [r for r in select(rows, area_f) if not r["address"].lower().startswith(a)]
        if not mine:
            h, _ = hook({**area_f, "kind": "area"}, rows, d, tl)
            return h, f"DROPPED own-listing hook: {f['addr']} no longer ranked; fell back to {label}"
        r = mine[0]
        gap = below(r)
        s = ((f"{r['address']} ranks #{r['rank']:,} of {n:,}: asking {money(r['ask'])}, worth about {money(r['worth'])}"
              if RANKED else
              f"{r['address']} is asking {money(r['ask'])}. Nearby sales and the county put it at about {money(r['worth'])}")
             + (f" as-is; {why_asis(r)}" if r.get("tier") not in (None, "none") else "") + ". ")
        if gap > 0.03:
            s += f"That's {pct(gap)} under value. "
        elif gap < -0.03:
            s += f"The records put the ask {pct(gap)} over value. "
        else:
            s += "So the ask holds up. "
        ls = last_sale(tl, r)
        if ls and ls["d"] >= "2025-01-01":
            s += f"It last sold for {money(ls['v'])} on {ls['d']}. "
        if area and "label" in f:
            s += f"Top of {label}: {home_line(area[0], n)}"
        return s.strip(), f"own listing #{r['rank']} (ask {money(r['ask'])} vs worth {money(r['worth'])})"

    # area
    sub = select(rows, f)
    if not sub:
        h, _ = hook({"kind": "metro"}, rows, d, tl)
        return h, f"no ranked homes in {label}; fell back to metro"
    k20 = sum(1 for r in sub if below(r) >= 0.2)
    # "N of them are <metro foreclosures>" vs "N of them are in <55406>"
    is_kind = re.search(r"homes|houses|units|condos|foreclosures", label)
    lead = f"{len(sub):,} of them are {'' if is_kind else 'in '}{label}"
    if k20:
        lead += f", and {k20:,} ask 20% or more under value"
    note = f"area n={len(sub)} top {sub[0]['address']} #{sub[0]['rank']}"
    if len(sub) < 3:
        note += " (THIN: under 3 homes)"
    return f"{lead}. Top of those: {home_line(sub[0], n)}", note


# ---------------------------------------------------------------- drafts


def words(s):
    return len(re.findall(r"[A-Za-z0-9$#%'.,-]+", s))


def subreddit(url):
    m = re.search(r"reddit\.com/r/([^/]+)", url)
    return m.group(1).lower() if m else ""


def render(row, h, n, pw):
    seg, ch = row["segment"], row["channel"]
    head = ["DRAFT — not sent. Aidan reviews before anything goes out.",
            f"To: {row['address_or_handle']}   ({ch})",
            f"Hook: {row['hook_url']}"]
    if row.get("time_sensitive"):
        head.append(f"TIME: {row['time_sensitive']}")

    if ch == "reddit":
        sub = subreddit(row["hook_url"])
        assert sub not in EXCLUDED_SUBS, sub
        rule = SUB_RULES.get(sub, "nolink")
        head.append(f"Rule: r/{sub} -> {rule} (reply in thread, never a new post)")
        if rule == "notool":
            h = h if h.startswith(row["focus"].split("addr=")[-1].split(";")[0]) else \
                f"Of the {n:,} metro homes for sale that county records and recent sales can value, " + h.replace(" of them are", " are", 1)
        body = [row["opener"], h]
        if rule == "nolink":
            body.append("That's from a ranking I built off county records and recent sales. "
                        "Happy to DM a free login to the full list if it helps.")
            follow = (f"\n---\nIf they ask, by DM:\n\nHere's your own password to the whole directory: {pw}\n{SITE}\n\n"
                      "If anything in it's wrong for the houses you're looking at, tell me.")
        else:
            follow = ""
        text = "\n\n".join(body)
        return "\n".join(head + ["Subject: (reply, no subject)", "", text]) + follow + "\n", words(text)

    if ch == "facebook-group":
        head.append("Rule: join and read the group's rules first; if promo posts are barred, skip it.")
        body = [row["greeting"] + ",", row["opener"],
                f"I ranked the {n:,} homes for sale in the seven-county metro by how far the ask sits under value. {h}",
                f"It's normally ${PRICE}. Here's a password for the group, free: {pw}\n{SITE}",
                ASK["community"]]
        text = "\n\n".join(body)
        return "\n".join(head + [f"Subject: {row['subject_v2']}", "", text, "", "— Aidan"]) + "\n", words(text)

    body = [row["greeting"] + ",", row["opener"],
            f"I ranked the {n:,} homes for sale in the seven-county metro by how far the ask sits under value. {h}",
            f"It's normally ${PRICE}. Here's your own password to the whole directory, free: {pw}\n{SITE}",
            ASK[seg]]
    text = "\n\n".join(body)
    sig = "\n\nBest,\nAidan Jude\naidanjude.substack.com"
    if ch == "form":
        head.append("Channel: their contact form; paste the body, name + email only, no invented fields.")
    if ch == "meetup-message":
        head.append("Channel: Meetup message to the organiser.")
    return "\n".join(head + [f"Subject: {row['subject_v2']}", "", text]) + sig + "\n", words(text) - 1


def password_for(label, cache, issue):
    if label in cache:
        return cache[label]
    script = ROOT / "scripts/issue_password.py"
    if issue and script.exists():
        out = subprocess.run([sys.executable, str(script), label], capture_output=True, text=True, check=True)
        pw = out.stdout.strip().splitlines()[-1].strip()
        cache[label] = pw
        return pw
    return "{{PASSWORD}}"


def main():
    issue = "--issue" in sys.argv
    d, rows, tl = load()
    n = len(rows)
    ledger = list(csv.DictReader(LEDGER.open()))
    fields = list(ledger[0].keys())
    cache = json.loads(PWCACHE.read_text()) if PWCACHE.exists() else {}
    DRAFTS.mkdir(exist_ok=True)
    long, counts, placeholders = [], {}, 0
    global RANKED
    for row in ledger:
        f = parse_focus(row["focus"])
        RANKED = not (row["channel"] == "reddit" and SUB_RULES.get(subreddit(row["hook_url"])) == "notool")
        h, note = hook(f, rows, d, tl)
        pw = password_for(row["password_label"], cache, issue)
        text, wc = render(row, h, n, pw)
        (OUT / row["draft_file"]).write_text(text)
        placeholders += "{{PASSWORD}}" in text
        row["hook_v2"], row["hook_check"], row["rendered_on"] = h, note, f"{date.today()} (index built {d['built']})"
        counts[row["segment"]] = counts.get(row["segment"], 0) + 1
        if row["channel"] in ("email", "form", "meetup-message") and wc > WORD_LIMIT:
            long.append((wc, row["draft_file"]))
    w = csv.DictWriter(LEDGER.open("w", newline=""), fieldnames=fields)
    w.writeheader()
    w.writerows(ledger)
    if cache:
        PWCACHE.write_text(json.dumps(cache, indent=1))
    print(f"{len(ledger)} drafts from an index of {n:,} (built {d['built']})", counts)
    if placeholders:
        print(f"{placeholders} drafts still carry {{{{PASSWORD}}}}: run with --issue once scripts/issue_password.py exists")
    for wc, fpath in sorted(long, reverse=True):
        print(f"  over {WORD_LIMIT} words: {wc} {fpath}")


if __name__ == "__main__":
    main()
