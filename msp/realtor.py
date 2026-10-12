"""Realtor.com's own web GraphQL (the endpoint its search and detail pages call).

Polite by construction: every response is cached on disk forever under
cache/realtor/ (delete a file to refetch), and uncached requests are spaced
DELAY seconds apart. Reverse-engineered in ~/Desktop/house-lookup (REA-NOTES.md).
"""
import hashlib, json, os, time
from pathlib import Path
import requests

GRAPHQL = "https://www.realtor.com/frontdoor/graphql"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
CACHE = Path(__file__).resolve().parent.parent / "cache" / "realtor"
DELAY = float(os.environ.get("REALTOR_DELAY", "1.5"))
_last = 0.0
_session = requests.Session()


def gql(query: str, client: str, key: str, refresh: bool = False) -> dict:
    """POST one GraphQL operation named Q. Cached by `key`."""
    global _last
    f = CACHE / (hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")
    if f.exists() and not refresh:
        return json.loads(f.read_text())["body"]
    wait = _last + DELAY - time.time()
    if wait > 0:
        time.sleep(wait)
    for attempt in range(5):
        _last = time.time()
        try:
            r = _session.post(GRAPHQL, json={"operationName": "Q", "variables": {}, "query": query},
                              headers={"User-Agent": UA, "Content-Type": "application/json",
                                       "rdc-client-name": client, "rdc-client-version": "2.0.0"}, timeout=60)
        except requests.RequestException:
            time.sleep(10 * (attempt + 1))
            continue
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else None
        transient = r.status_code >= 429 or (body and body.get("errors") and any(w in str(body["errors"]).lower() for w in ("abort", "timeout")))
        if not transient:
            break
        time.sleep(10 * (attempt + 1))  # 400 = a bad query: retrying won't help, surface it
    if body is None:
        r.raise_for_status()
    if body.get("errors"):
        raise RuntimeError(f"{key}: {body['errors'][0]['message']}")
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"at": time.time(), "key": key, "body": body}))
    return body
