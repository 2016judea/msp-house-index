"""Which words in a listing mean the house sold below its comps? Measured, not guessed.

For every closed sale before the backtest holdout that has its listing text, the
residual is log(sale price ÷ the plain comp value as of the day before it sold)
(msp/valuation.py baseline(): same kind, size and age, nearest 10). A phrase's
effect is the median residual of the sales whose text has it, minus the median of
all sales. Two lists are scored:

  CANDIDATES  phrases an agent uses for a house that needs work, written down first
  mined       every 2-4 word phrase in 40+ listings, for the ones we didn't think of

    .venv/bin/python scripts/mine_condition.py   # writes data/condition_phrases.json, prints the ranking

msp/valuation.py reads the file: a phrase whose measured effect is -15% or worse marks
"needs work", -5% to -15% marks "dated". Mined phrases enter only via CANDIDATES after
a look (a mined phrase can be a place or a deal term, not condition).
"""
import json, re, sys, collections, datetime as dt
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from msp import valuation as V

D = Path(__file__).resolve().parent.parent / "data"
TODAY = dt.date.today()
CUT = (TODAY - dt.timedelta(days=90)).isoformat()

CANDIDATES = [
    "renovation", "ready for renovation", "possibilities for renovation", "customization", "customize", "bring your vision",
    "bring your ideas", "bring your imagination", "your vision", "make it your own", "make it yours", "put your own touch",
    "personal touches", "investor", "investors", "investor special", "cash only", "cash offers", "cash or rehab", "rehab",
    "estate sale", "estate", "trust sale", "probate", "original condition", "original owner", "original owners", "one owner",
    "tlc", "needs updating", "needs updates", "needs work", "needs some work", "in need of", "update to your taste",
    "updating", "sold as-is", "as-is", "as is", "fixer", "handyman", "sweat equity", "diamond in the rough", "potential",
    "great bones", "good bones", "solid bones", "opportunity", "incredible opportunity", "priced below", "below assessed",
    "below county", "assessed value", "price reflects", "no repairs", "will not make any repairs", "repairs", "contractor",
    "builders", "build equity", "equity", "time capsule", "vintage", "retro", "dated", "cosmetic", "mold", "water damage",
    "fire damage", "foundation", "roof needs", "needs a new roof", "not fha", "conventional or cash", "short sale",
    "foreclosure", "bank owned", "lender owned", "hud", "reo", "tear down", "teardown", "lot value", "land value",
    "renovated", "remodeled", "updated", "fully updated", "completely remodeled", "new kitchen", "move-in ready",
    "move in ready", "turnkey", "brand new", "newer roof", "new roof", "new furnace", "new windows",
]


# Named groups: euphemisms too rare one by one, measured together. Written 2026-10-11
# off 8031 Tierneys Woods Curv ("endless possibilities for renovation", "ready for
# renovation", "priced $200,000 below County assessed value"), valued at $921k, asking $500k.
GROUPS = {
    "renovation euphemism": r"\b(?:ready for (?:renovation|updating|updates|your updates|a remodel)|possibilities for renovation|renovation (?:project|opportunity)|"
                            r"needs? (?:a )?(?:full |complete |total )?(?:renovation|remodel|updating|updates)|awaits? (?:your )?(?:renovation|updates|remodel)|"
                            r"in need of (?:updating|updates|renovation|repair)|update to your (?:taste|liking|style)|restore,? redesign|starting point)\b",
    "priced under the county": r"\b(?:below|under) (?:the )?(?:county|city|assessed|tax)(?: assessed)?(?: value| valuation)?\b|\bbelow (?:its )?assessment\b",
    "as-is sale": r"\b(?:sold|selling|being sold|offered|sale) (?:in )?[\"'“]?as[- ]is\b|\bas[- ]is[\"'”]? (?:condition|sale)\b|will not (?:make|do) (?:any )?repairs|no repairs",
    "investor / cash": r"\b(?:investor|investors|cash only|cash offers only|cash or rehab|rehab loan|not (?:fha|va|financeable)|conventional or cash)\b",
    "build / land": r"\b(?:looking to build|build your (?:dream|own|new) home|tear ?down|lot value|land value|value is in the land|buildable lot)\b",
    "estate / probate": r"\b(?:estate sale|probate|trust sale|personal representative|court approval)\b",
    "original / dated": r"\b(?:original condition|time capsule|lovingly maintained by (?:the )?original|dated|original (?:kitchen|bath|baths|carpet|finishes))\b",
    "needs work": r"\b(?:tlc|fixer(?:[- ]upper)?|handyman(?: special)?|rehab|needs? (?:some |a lot of )?(?:work|repairs?)|in need of|repairs needed|sweat equity)\b",
    "cosmetic / personal touches": r"\b(?:cosmetic (?:updates|work|improvements)|personal touches|your (?:own )?personal touch|needs? (?:some )?updating|could use (?:some )?updat\w*|"
                                   r"bring your (?:ideas|own ideas)|make it your own|update to your)\b",
    "renovated": r"\b(?:(?:completely|fully|totally|extensively|newly|beautifully|tastefully|thoughtfully|meticulously) (?:renovated|remodeled|remodelled|updated|rehabbed)|"
                 r"(?:full|complete|total|gut|top[- ]to[- ]bottom) (?:renovation|remodel|rehab)|renovated in 20\d\d|remodeled in 20\d\d)\b",
    "bones / potential": r"\b(?:good bones|great bones|solid bones|diamond in the rough|sweat equity|tons of potential|lots of potential|full of potential|endless possibilities|bring your (?:vision|ideas|imagination|tools|contractor|paintbrush))\b",
}


def rx(p):
    return re.compile(r"\b" + re.escape(p).replace(r"\-", "[- ]").replace(r"\ ", r"[\s-]+") + r"\b", re.I)


def ngrams(words, lo=2, hi=4):
    out = set()
    for n in range(lo, hi + 1):
        for i in range(len(words) - n + 1):
            out.add(" ".join(words[i:i + n]))
    return out


def main():
    pool = V.Pool()
    res, texts = [], []
    for i, r in enumerate(pool.rows):
        if r["date"] >= CUT or not r.get("text") or r["type_group"] not in ("house", "townhome", "condo"):
            continue
        s = dict(r, x=pool.x[i], y=pool.y[i])
        b = V.baseline(s, pool, r["date"])
        if b:
            res.append(np.log(r["price"] / b))
            texts.append(r["text"])
    res = np.array(res)
    med = float(np.median(res))
    print(f"{len(res)} sales with text and a comp value, before {CUT}; median residual {med:+.3f}")

    def score(hits):
        h = res[hits]
        if len(h) < 20:
            return None
        rest = res[~hits]
        se = np.sqrt(h.var() / len(h) + rest.var() / len(rest))
        return {"n": int(len(h)), "effect": round(float(np.median(h)) - med, 4), "mean_effect": round(float(h.mean() - rest.mean()), 4),
                "t": round(float((h.mean() - rest.mean()) / se), 1)}

    groups = {}
    for name, pat in GROUPS.items():
        r_ = re.compile(pat, re.I)
        hits = np.array([bool(r_.search(t)) for t in texts])
        sc = score(hits)
        if sc:
            groups[name] = dict(sc, pattern=pat)
    cand = {}
    for p in CANDIDATES:
        r_ = rx(p)
        hits = np.array([bool(r_.search(t)) for t in texts])
        sc = score(hits)
        if sc:
            cand[p] = sc
    # Mined: every 2-4 word phrase in 40+ listings.
    toks = [re.findall(r"[a-z][a-z'-]+", t.lower()) for t in texts]
    grams = [ngrams(w) for w in toks]
    cnt = collections.Counter(g for gs in grams for g in gs)
    common = [g for g, c in cnt.items() if c >= 40]
    idx = collections.defaultdict(list)
    for j, gs in enumerate(grams):
        for g in gs:
            if g in cnt and cnt[g] >= 40:
                idx[g].append(j)
    mined = {}
    for g in common:
        hits = np.zeros(len(res), bool)
        hits[idx[g]] = True
        sc = score(hits)
        if sc and sc["effect"] <= -0.08 and sc["t"] <= -4:
            mined[g] = sc
    out = {"built": TODAY.isoformat(), "before": CUT, "n_sales": int(len(res)), "median_residual": round(med, 4),
           "groups": dict(sorted(groups.items(), key=lambda kv: kv[1]["effect"])),
           "candidates": dict(sorted(cand.items(), key=lambda kv: kv[1]["effect"])),
           "mined": dict(sorted(mined.items(), key=lambda kv: kv[1]["effect"])[:200])}
    (D / "condition_phrases.json").write_text(json.dumps(out, indent=1))
    print("\nGROUPS")
    for p, sc in out["groups"].items():
        print(f"  {sc['effect']:+.1%}  n={sc['n']:5}  t={sc['t']:+6.1f}  {p}")
    print("\nCANDIDATES (effect = median sale vs comps, against all sales)")
    for p, sc in out["candidates"].items():
        print(f"  {sc['effect']:+.1%}  n={sc['n']:5}  t={sc['t']:+6.1f}  {p}")
    print("\nMINED, strongest 80")
    for p, sc in list(out["mined"].items())[:80]:
        print(f"  {sc['effect']:+.1%}  n={sc['n']:5}  t={sc['t']:+6.1f}  {p}")


if __name__ == "__main__":
    main()
