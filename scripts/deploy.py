#!/usr/bin/env python3
"""Deploy site/ to Vercel over the REST API (no CLI). Needs VERCEL_TOKEN.
site/ is the public top 10 plus api/; private/bundle/ (the paid set) goes up as api/_full/,
bundled with /api/data and redirected away from any public URL (site/vercel.json).
Enables Web Analytics on the project and prints the production aliases."""
import hashlib, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests

TEAM = "team_XnhPzBEcaSfEmZFo9M2isLFY"
PROJECT = "msp-house-index"
# SITE_DIR deploys another copy of site/ (e.g. a clean `git archive` when the tree holds another session's work)
SITE = Path(os.environ.get("SITE_DIR") or Path(__file__).resolve().parent.parent / "site")
H = {"Authorization": f"Bearer {os.environ['VERCEL_TOKEN']}"}
API = "https://api.vercel.com"

# ~10k files since the block map (site/map/): upload 16 at a time, each sent once per content hash.
BUNDLE = Path(__file__).resolve().parent.parent / "private" / "bundle"


def upload(item):
    p, rel = item
    b = p.read_bytes()
    sha = hashlib.sha1(b).hexdigest()
    for _ in range(4):
        try:
            r = S.post(f"{API}/v2/files?teamId={TEAM}", data=b, headers={**H, "x-vercel-digest": sha, "Content-Type": "application/octet-stream"}, timeout=60)
            if r.ok:
                break
        except requests.RequestException:
            pass
        time.sleep(2)
    else:
        sys.exit(f"upload failed: {p}")
    return {"file": rel, "sha": sha, "size": len(b)}


S = requests.Session()
S.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=16))
assert (BUNDLE / "rows.json.gz").exists(), "run make build: private/bundle/ is what the paywall serves"
paths = [(p, str(p.relative_to(SITE))) for p in sorted(SITE.rglob("*")) if p.is_file() and not p.name.startswith(".") and "_full" not in p.parts]
# The paid bundle goes up SEALED (msp/seal.py says why): a fresh key per deploy, set on the
# project before the deployment is created so the function is built with it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tempfile
from msp.seal import new_key, seal
KEY = new_key()
r = requests.post(f"{API}/v10/projects/{PROJECT}/env?teamId={TEAM}&upsert=true", headers=H,
                  json={"key": "BUNDLE_KEY", "value": KEY.hex(), "type": "encrypted", "target": ["production", "preview"]})
assert r.ok, f"could not set BUNDLE_KEY: {r.status_code} {r.text[:200]}"
SEALED = Path(tempfile.mkdtemp(prefix="msp-sealed-"))
for p in sorted(BUNDLE.rglob("*")):
    if p.is_file():
        out = SEALED / p.relative_to(BUNDLE)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(seal(p.read_bytes(), KEY))
paths += [(p, "api/_full/" + str(p.relative_to(SEALED))) for p in sorted(SEALED.rglob("*")) if p.is_file()]
with ThreadPoolExecutor(16) as ex:
    files = list(ex.map(upload, paths))
print(f"uploaded {len(files)} files")
d = requests.post(f"{API}/v13/deployments?teamId={TEAM}&forceNew=1", headers=H, json={
    "name": PROJECT, "project": PROJECT, "target": "production", "files": files,
    "projectSettings": {"framework": None, "buildCommand": "echo build", "outputDirectory": "public", "installCommand": "echo skip"},
}).json()
print("deployment", d.get("id"), d.get("readyState"), d.get("error"))
for _ in range(200):
    s = requests.get(f"{API}/v13/deployments/{d['id']}?teamId={TEAM}", headers=H).json()
    if s.get("readyState") in ("READY", "ERROR"):
        break
    time.sleep(3)
print("state", s.get("readyState"))
prj = requests.get(f"{API}/v9/projects/{PROJECT}?teamId={TEAM}", headers=H).json()
if not (prj.get("webAnalytics") or {}).get("enabledAt"):
    print("analytics", requests.post(f"{API}/web/insights/toggle?projectId={prj['id']}&teamId={TEAM}", headers=H, json={"value": True}).json())
print("aliases", (prj.get("targets", {}).get("production") or {}).get("alias"))
