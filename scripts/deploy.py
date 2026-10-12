#!/usr/bin/env python3
"""Deploy site/ to Vercel over the REST API (no CLI). Needs VERCEL_TOKEN.
Enables Web Analytics on the project and prints the production aliases."""
import hashlib, json, os, sys, time
from pathlib import Path
import requests

TEAM = "team_XnhPzBEcaSfEmZFo9M2isLFY"
PROJECT = "msp-house-index"
SITE = Path(__file__).resolve().parent.parent / "site"
H = {"Authorization": f"Bearer {os.environ['VERCEL_TOKEN']}"}
API = "https://api.vercel.com"

files = []
for p in sorted(SITE.rglob("*")):
    if p.is_file() and not p.name.startswith("."):
        b = p.read_bytes()
        sha = hashlib.sha1(b).hexdigest()
        requests.post(f"{API}/v2/files?teamId={TEAM}", data=b, headers={**H, "x-vercel-digest": sha, "Content-Type": "application/octet-stream"})
        files.append({"file": str(p.relative_to(SITE)), "sha": sha, "size": len(b)})
d = requests.post(f"{API}/v13/deployments?teamId={TEAM}&forceNew=1", headers=H, json={
    "name": PROJECT, "project": PROJECT, "target": "production", "files": files,
    "projectSettings": {"framework": None, "buildCommand": "echo build", "outputDirectory": ".", "installCommand": "echo skip"},
}).json()
print("deployment", d.get("id"), d.get("readyState"), d.get("error"))
for _ in range(60):
    s = requests.get(f"{API}/v13/deployments/{d['id']}?teamId={TEAM}", headers=H).json()
    if s.get("readyState") in ("READY", "ERROR"):
        break
    time.sleep(3)
print("state", s.get("readyState"))
prj = requests.get(f"{API}/v9/projects/{PROJECT}?teamId={TEAM}", headers=H).json()
if not (prj.get("webAnalytics") or {}).get("enabledAt"):
    print("analytics", requests.post(f"{API}/web/insights/toggle?projectId={prj['id']}&teamId={TEAM}", headers=H, json={"value": True}).json())
print("aliases", (prj.get("targets", {}).get("production") or {}).get("alias"))
