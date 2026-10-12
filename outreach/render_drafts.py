"""Render the outreach drafts from the ledger and the current index.

    python outreach/render_drafts.py            # write outreach/drafts/*.md, update ledger hook columns
    python outreach/render_drafts.py --issue    # also mint a password per recipient (scripts/issue_password.py)

Nothing here sends anything. Every draft is for Aidan to read first.

The index ranks by ROOM TO NEGOTIATE: each listing's predicted sale price (from how recent
closed sales went against their ask: days listed, cuts, the ask against the records value,
area, condition words, HOA), and the rank is ask minus predicted sale. The records value is
evidence on each home ("Records say it's worth..."), never the headline. So no draft claims a
home is "worth $X" or "N% under value"; it says how a home is likely to sell against its ask.

Inputs (all local, all gitignored except this script):
  outreach/ledger.csv     one row per recipient. Columns this script reads:
                          segment, channel, address_or_handle, hook_url, draft_file,
                          greeting, subject_v2, opener (the reader's OWN public record, written by hand),
                          focus (what slice of the index to describe, see FOCUS below), password_label
  private/data.json       the full ranked set (make build)
  data/backtest.json      the tested ordering claim (sale_list.deciles on homes sold since holdout_from)

Every number in a draft is computed here, never typed. Re-run after any rebuild.

FOCUS is `key=value` pairs joined by `;`:
  kind=area|own|metro|cuts|market
  zip=55411,55412  city=Saint Paul,Minneapolis  county=hennepin  type=house|multi|condo|townhome
  maxask=300000  tier=light,heavy,condemned  signal=foreclosure  addr=<street address> (kind=own)
  label=<how the slice is named in the email>
"""
from __future__ import annotations

import csv
import json
import re
import statistics
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

# Segments whose work the tested ordering claim speaks to (it is a claim about the ranking itself).
CLAIM_SEGMENTS = {"reporter", "lender", "investor-hub"}

# Subreddit rules, re-read 2026-10-11 from the ledger notes.
#   nolink: rule bars commerce/advertising; a paid tool link is commerce now there is a paywall.
#           Reply answers the question; the password goes by DM only if they ask.
#   notool: rule bars self-promotion; the reply answers from the records and never names the tool.
SUB_RULES = {"twincities": "nolink", "saintpaul": "nolink", "firsttimehomebuyer": "notool"}
# Never post in r/realestateinvesting or r/RealEstate (both ban it outright; kept from v1).
EXCLUDED_SUBS = {"realestateinvesting", "realestate"}

ASK = {
    "reporter": "What's wrong in it? What's missing before you'd quote it?",
    "investor-hub": "What's wrong, and what's missing for the investors you work with?",
    "flipper": "What's wrong, and what's missing before you'd buy off it?",
    "lender": "Where does it miss what you see at closing? And what's missing?",
    "counsellor": "What's wrong with it, and what would your buyers need from it?",
    "community": "If something in it looks wrong to an investor, tell me. Same for what's missing.",
}

# ---------------------------------------------------------------- data


def load():
    d = json.loads((ROOT / "private/data.json").read_text())
    rows = sorted(d["rows"], key=lambda r: r["rank"])
    for r in rows:
        r.setdefault("room", r["ask"] - r["worth"])
        r["room_pct"] = r["room"] / r["ask"]
    bt = json.loads((ROOT / "data/backtest.json").read_text())
    return d, rows, bt


def claim(bt):
    """The tested ordering claim, from the backtest's deciles."""
    sl = bt["sale_list"]
    dec = sorted(sl["deciles"], key=lambda x: x["decile"])
    most, least = dec[0]["actual_sale_vs_ask"], dec[-1]["actual_sale_vs_ask"]
    since = date.fromisoformat(bt["holdout_from"]).strftime("%B")
    return (f"On {sl['holdout_n']:,} homes sold since {since}, the tenth I'd have ranked with the most room sold "
            f"{abs(most) * 100:.1f}% {'under' if most < 0 else 'over'} ask, and the tenth with the least "
            f"{abs(least) * 100:.1f}% {'over' if least > 0 else 'under'}.")


def money(v):
    if v is None:
        return "?"
    if v >= 995_000:
        return f"${v / 1e6:.2f}M"
    return f"${round(v / 1000):,}k"


def pct(p):
    p = abs(p) * 100
    return f"{p:.0f}%" if p >= 2 else f"{p:.1f}%"


def room_phrase(r):
    p = r["room_pct"]
    if p >= 0.005:
        return f"likely to sell {pct(p)} under its {money(r['ask'])} ask"
    if p <= -0.005:
        return f"likely to sell {pct(p)} over its {money(r['ask'])} ask"
    return f"likely to sell right at its {money(r['ask'])} ask"


def listed(r):
    c = r.get("n_cuts") or 0
    cuts = "no cuts" if not c else f"{c} cut{'s' if c != 1 else ''}"
    return f"{r['dom']:,} days listed, {cuts}"


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
        return True

    return [r for r in rows if ok(r)]


def why_work(r):
    """Plain reason the records say a home needs work, from its strongest tier fact."""
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
    return ""


def home_line(r, work=False):
    where = f"{r['address']}, {r['city'].replace('Saint ', 'St. ')}"
    s = f"{where}: {listed(r)}, {room_phrase(r)}."
    if work and r.get("tier") not in (None, "none") and why_work(r):
        w = why_work(r)
        s += f" {w[0].upper() + w[1:]}."
    return s


# ---------------------------------------------------------------- hooks


def hook(f, rows, d, bt, with_claim):
    """Return (hook_text, check_note)."""
    n = len(rows)
    kind = f.get("kind", "area")
    label = f.get("label", "the metro")
    work = "tier" in f or bool(f.get("work"))

    if kind == "market":
        under = sum(1 for r in rows if r["room_pct"] >= 0.005)
        over = sum(1 for r in rows if r["room_pct"] <= -0.005)
        tenth = max(1, n // 10)
        top, bot = rows[:tenth], rows[-tenth:]
        dt, db = statistics.median(r["dom"] for r in top), statistics.median(r["dom"] for r in bot)
        return (f"Of the {n:,} homes for sale I could rank across the seven-county metro, {under:,} are likely to sell under "
                f"their ask and {over:,} over it. The tenth with the most room have sat a median {dt:.0f} days. "
                f"The tenth likely to go over have sat {db:.0f}.",
                f"market recomputed: {under} under / {over} over, dom {dt:.0f} vs {db:.0f}")

    if kind == "metro":
        top = rows[0]
        return f"#1 right now is {home_line(top)}", f"metro top: {top['address']} #{top['rank']}"

    if kind == "cuts":
        sub = [r for r in rows if (r.get("n_cuts") or 0) >= 3]
        k = sum(1 for r in sub if r["room_pct"] >= 0.005)
        return (f"{len(sub):,} have cut their price three times or more, and {k:,} of those are still likely to sell under ask. "
                f"Most room: {home_line(sub[0])}", f"3+ cuts top: {sub[0]['address']} #{sub[0]['rank']}")

    if kind == "own":
        a = f["addr"].lower()
        mine = [r for r in rows if r["address"].lower().startswith(a)]
        area_f = {k: v for k, v in f.items() if k not in ("addr", "kind")}
        area = [r for r in select(rows, area_f) if not r["address"].lower().startswith(a)]
        if not mine:
            h, _ = hook({**area_f, "kind": "area"}, rows, d, bt, with_claim)
            return h, f"DROPPED own-listing hook: {f['addr']} no longer ranked; fell back to {label}"
        r = mine[0]
        rank = f", #{r['rank']:,} of {n:,}," if RANKED else ""
        s = f"{r['address']}{rank} has been listed {r['dom']:,} days and is {room_phrase(r)}."
        if r.get("worth_est"):
            s += f" Records say it's worth about {money(r['worth_est'])}"
            s += ", but homes like it are selling at their ask." if abs(r["room_pct"]) < 0.02 and r["worth_est"] > r["ask"] * 1.1 else "."
        if area and "label" in f:
            prep = "among" if re.search(r"homes|houses|units|condos|foreclosures", label) else "in"
            s += f" Most room {prep} {label}: {home_line(area[0], work)}"
        return s, f"own listing #{r['rank']} ({pct(r['room_pct'])} {'under' if r['room_pct'] >= 0 else 'over'} ask)"

    # area
    sub = select(rows, f)
    if not sub:
        h, _ = hook({"kind": "metro"}, rows, d, bt, with_claim)
        return h, f"no ranked homes in {label}; fell back to metro"
    top = sub[0]
    note = f"area n={len(sub)} top {top['address']} #{top['rank']} room {pct(top['room_pct'])}"
    if len(sub) < 3:
        note += " (THIN: under 3 homes)"
    if top["room_pct"] < 0.005:
        note += " (NO ROOM: best in area sells at/over ask)"
    is_kind = re.search(r"homes|houses|units|condos|foreclosures", label)
    if with_claim:  # the claim already costs ~35 words; skip the count
        return f"Most room {'among' if is_kind else 'in'} {label}: {home_line(top, work)}", note
    k = sum(1 for r in sub if r["room_pct"] >= 0.005)
    lead = f"{len(sub):,} of them are {'' if is_kind else 'in '}{label}, and {k:,} are likely to sell under ask"
    return f"{lead}. Most room: {home_line(top, work)}", note


# ---------------------------------------------------------------- drafts


def words(s):
    return len(re.findall(r"[A-Za-z0-9$#%'.,-]+", s))


def subreddit(url):
    m = re.search(r"reddit\.com/r/([^/]+)", url)
    return m.group(1).lower() if m else ""


def pw_lines(pw, group=False):
    who = "a password for the group" if group else "your own password"
    link = f"{SITE}/?pw={pw}" if pw != "{{PASSWORD}}" else SITE
    return f"It's normally ${PRICE}. Here's {who}, free: {pw}\n{link}"


def render(row, h, n, pw, bt):
    seg, ch = row["segment"], row["channel"]
    head = ["DRAFT — not sent. Aidan reviews before anything goes out.",
            f"To: {row['address_or_handle']}   ({ch})",
            f"Hook: {row['hook_url']}",
            f"Password label: {row['password_label']}"]
    if row.get("time_sensitive"):
        head.append(f"TIME: {row['time_sensitive']}")
    bridge = f"I ranked the {n:,} homes for sale in the metro by room to negotiate: how far each is likely to sell under its ask."

    if ch == "reddit":
        sub = subreddit(row["hook_url"])
        assert sub not in EXCLUDED_SUBS, sub
        rule = SUB_RULES.get(sub, "nolink")
        head.append(f"Rule: r/{sub} -> {rule} (reply in thread, never a new post)")
        if rule == "notool" and not row["focus"].startswith("kind=own"):
            h = (f"Going by how recent sales landed against their ask, " + h[0].lower() + h[1:])
        body = [row["opener"], h]
        follow = ""
        if rule == "nolink":
            body.append("That's from a ranking I built off recent sales and county records. "
                        "Happy to DM a free login to the full list if it helps.")
            follow = (f"\n---\nIf they ask, by DM:\n\nHere's your own password to the whole directory: {pw}\n"
                      f"{SITE}/?pw={pw}\n\nIf anything in it's wrong for the houses you're looking at, tell me.")
        text = "\n\n".join(body)
        return "\n".join(head + ["Subject: (reply, no subject)", "", text]) + follow + "\n", words(text)

    if ch == "facebook-group":
        head.append("Rule: join and read the group's rules first; if promo posts are barred, skip it.")
        body = [row["greeting"] + ",", row["opener"], f"{bridge} {h}", pw_lines(pw, group=True), ASK["community"]]
        text = "\n\n".join(body)
        return "\n".join(head + [f"Subject: {row['subject_v2']}", "", text, "", "— Aidan"]) + "\n", words(text)

    mid = f"I ranked the {n:,} homes for sale in the metro by room to negotiate. {claim(bt)} {h}" \
        if seg in CLAIM_SEGMENTS else f"{bridge} {h}"
    body = [row["greeting"] + ",", row["opener"], mid, pw_lines(pw), ASK[seg]]
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
        out = subprocess.run([sys.executable, str(script), label], capture_output=True, text=True, check=True, cwd=ROOT)
        m = re.search(r"password\s+(\S+)", out.stdout)
        if not m:
            sys.exit(f"no password in issue_password.py output for {label}:\n{out.stdout}")
        cache[label] = m.group(1)
        return cache[label]
    return "{{PASSWORD}}"


def main():
    global RANKED
    issue = "--issue" in sys.argv
    d, rows, bt = load()
    n = len(rows)
    ledger = list(csv.DictReader(LEDGER.open()))
    fields = list(ledger[0].keys())
    cache = json.loads(PWCACHE.read_text()) if PWCACHE.exists() else {}
    DRAFTS.mkdir(exist_ok=True)
    long, counts, placeholders = [], {}, 0
    for row in ledger:
        f = parse_focus(row["focus"])
        RANKED = not (row["channel"] == "reddit" and SUB_RULES.get(subreddit(row["hook_url"])) == "notool")
        with_claim = row["segment"] in CLAIM_SEGMENTS and row["channel"] not in ("reddit", "facebook-group")
        h, note = hook(f, rows, d, bt, with_claim)
        notool = row["channel"] == "reddit" and not RANKED
        pw = "" if notool else password_for(row["password_label"], cache, issue)
        text, wc = render(row, h, n, pw, bt)
        (OUT / row["draft_file"]).write_text(text)
        placeholders += "{{PASSWORD}}" in text
        row["hook_v2"], row["hook_check"] = h, note
        row["rendered_on"] = f"{date.today()} (index built {d['built']}, backtest {bt['built']})"
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
        print(f"{placeholders} drafts still carry {{{{PASSWORD}}}}: run with --issue")
    for wc, fpath in sorted(long, reverse=True):
        print(f"  over {WORD_LIMIT} words: {wc} {fpath}")


if __name__ == "__main__":
    main()
