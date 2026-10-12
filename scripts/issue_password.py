#!/usr/bin/env python3
"""A complimentary password to the full index, one per person, no Stripe session behind it.

    python scripts/issue_password.py "comp:frederick-melo"

The same label always gives the same password. /api/data accepts it because its last seven
characters are an HMAC of the first five under ACCESS_SECRET in the "comp:" domain (site/api/_lib.js
checkPassword). Every label issued is logged to private/comp_passwords.csv (gitignored) and to
private/bundle/passes.json, which deploy.py ships beside /api/data so each unlock logs its label
(Vercel function logs, "[unlock] comp <label>"). A label issued after the last deploy still
unlocks; its log line shows the nonce until the next deploy.
"""
import csv, datetime as dt, hashlib, hmac, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALPHA = "23456789BCDFGHJKMNPQRSTVWXYZ"  # same as site/api/_lib.js
LOG, PASSES = ROOT / "private/comp_passwords.csv", ROOT / "private/bundle/passes.json"


def secret():
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("ACCESS_SECRET="):
            return line.split("=", 1)[1].strip().encode()
    sys.exit("ACCESS_SECRET missing from .env")


def chars(b, n):
    return "".join(ALPHA[x % len(ALPHA)] for x in b[:n])


def comp_password(label):
    s = secret()
    nonce = chars(hmac.new(s, ("label:" + label).encode(), hashlib.sha256).digest(), 5)
    raw = nonce + chars(hmac.new(s, ("comp:" + nonce).encode(), hashlib.sha256).digest(), 7)
    return nonce, f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


if len(sys.argv) != 2 or not sys.argv[1].strip():
    sys.exit(__doc__)
label = sys.argv[1].strip()
nonce, pw = comp_password(label)
LOG.parent.mkdir(exist_ok=True)
new = not LOG.exists()
with LOG.open("a", newline="") as f:
    w = csv.writer(f)
    if new:
        w.writerow(["issued", "label", "nonce"])
    w.writerow([dt.date.today().isoformat(), label, nonce])
PASSES.parent.mkdir(parents=True, exist_ok=True)
passes = json.loads(PASSES.read_text()) if PASSES.exists() else {}
passes[nonce] = label
PASSES.write_text(json.dumps(passes, indent=1))
print(f"{label}\n  password {pw}\n  link     https://msp-house-index.vercel.app/?pw={pw}")
