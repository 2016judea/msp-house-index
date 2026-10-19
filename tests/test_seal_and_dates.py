import gzip
import os
import pathlib
import subprocess

import pytest

from msp.seal import new_key, seal, unseal

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_sealed_bundle_is_not_readable_without_the_key():
    raw = gzip.compress(b'{"rows":[{"address":"345 6th Ave N"}]}')
    key = new_key()
    blob = seal(raw, key)
    assert b"345 6th" not in blob and not blob.startswith(b"\x1f\x8b"), "ciphertext, not gzip"
    assert unseal(blob, key) == raw
    with pytest.raises(Exception):
        unseal(blob, new_key())


def test_the_paid_route_opens_a_python_sealed_bundle(tmp_path):
    """site/api/data.js must decrypt exactly what scripts/deploy.py uploads (msp/seal.py)."""
    key = new_key()
    body = b'{"built":"x","rows":[{"property_id":"1"}]}'
    (tmp_path / "rows.json.gz").write_bytes(seal(gzip.compress(body), key))
    (tmp_path / "detail.json.gz").write_bytes(seal(gzip.compress(b'{"1":{"ev":["a"]}}'), key))
    (tmp_path / "passes.json").write_bytes(seal(b'{}', key))
    js = r"""
const { mac } = (()=>{const c=require("crypto");const A="23456789BCDFGHJKMNPQRSTVWXYZ";
  const ch=(b,n)=>Array.from(b.subarray(0,n),x=>A[x%A.length]).join("");
  return {mac:(n,d)=>ch(c.createHmac("sha256",process.env.ACCESS_SECRET).update(d+n).digest(),7)}})();
const raw="ABCDE"+mac("ABCDE","comp:"), pw=raw.slice(0,4)+"-"+raw.slice(4,8)+"-"+raw.slice(8);
const h=require(process.argv[1]);
const zlib=require("zlib");
const res=(cb)=>{const r={headers:{},code:0,setHeader(k,v){this.headers[k]=v},status(c){this.code=c;return this},
  json(o){cb(this.code,JSON.stringify(o))},end(b){cb(this.code,zlib.gunzipSync(b).toString())}};return r};
(async()=>{
  await h({headers:{"x-password":pw},query:{}},res((c,b)=>console.log("rows",c,b)));
  await h({headers:{"x-password":pw},query:{id:"1"}},res((c,b)=>console.log("detail",c,b)));
  await h({headers:{"x-password":"BAD"},query:{}},res((c,b)=>console.log("bad",c)));
})();
"""
    env = {**os.environ, "ACCESS_SECRET": "s3", "BUNDLE_KEY": key.hex(), "FULL_DIR": str(tmp_path)}
    out = subprocess.run(["node", "-e", js, str(ROOT / "site/api/data.js")], capture_output=True, text=True, env=env, timeout=30)
    assert out.returncode == 0, out.stderr
    lines = out.stdout
    assert 'rows 200 {"built":"x"' in lines, lines + out.stderr
    assert 'detail 200 {"ev":["a"]}' in lines, lines
    assert "bad 401" in lines


def test_vercel_redirect_covers_the_percent_encoded_underscore():
    cfg = (ROOT / "site/vercel.json").read_text()
    assert "/api/%5Ffull/:path*" in cfg


def test_list_date_is_the_minneapolis_date_not_the_utc_date():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bi", ROOT / "scripts/build_index.py")
    bi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bi)
    # 01:30Z on the 12th is 20:30 CDT on the 11th
    assert bi.local_date("2026-10-12T01:30:00.000000Z") == "2026-10-11"
    assert bi.local_date("2026-10-11T19:19:23.000000Z") == "2026-10-11"
    assert bi.local_date("2026-10-11") == "2026-10-11"
    assert bi.local_date(None) is None
