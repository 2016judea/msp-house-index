// GET /api/data            -> the full index (every ranked home), gzipped JSON
// GET /api/data?id=<pid>   -> one home's evidence, records and timeline
// GET /api/data?cell=<x_y> -> one block-map cell
// Password in the x-password header (or ?pw=). The data is bundled with this function
// (api/_full/, uploaded by scripts/deploy.py from private/bundle/), never in public site/.
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");
const { checkPassword, nonceIsPaid, open } = require("./_lib");

const DIR = process.env.FULL_DIR || path.join(__dirname, "_full");
let ROWS_GZ = null, DETAIL = null, PASSES = null;
const MAP = {};

module.exports = async (req, res) => {
  const pw = req.headers["x-password"] || req.query.pw;
  const ok = checkPassword(pw);
  if (!ok) return res.status(401).json({ error: "That password didn't match. Check the email from Brick & Mortar, or reply to it for help." });
  try {
    if (ok.kind === "paid" && !(await nonceIsPaid(ok.nonce))) return res.status(402).json({ error: "That password's payment hasn't gone through. If you just paid, wait a minute and try again." });
  } catch (e) {
    console.error("[data] paid check:", e.message);
    return res.status(502).json({ error: "Couldn't check the password right now. Try again in a minute." });
  }
  res.setHeader("Cache-Control", "private, no-store");
  res.setHeader("X-Access", ok.kind);
  const cellKey = String(req.query.cell || "");
  if (cellKey) {
    const m = /^(\d+)_(\d+)$/.exec(cellKey);
    if (!m) return res.status(400).json({ error: "bad cell" });
    const i = (Number(m[1]) * 31 + Number(m[2])) % 64; // scripts/build_public.py shard()
    const pack = (MAP[i] ??= JSON.parse(zlib.gunzipSync(open(fs.readFileSync(path.join(DIR, "map", `${i}.json.gz`))))));
    return pack[cellKey] ? res.status(200).json(pack[cellKey]) : res.status(404).json({ error: "no such cell" });
  }
  const id = req.query.id;
  if (!id) {
    // Who unlocked: the comp's label (private/comp_passwords.csv, bundled as passes.json), never the password.
    if (ok.kind === "comp") {
      try { PASSES ??= JSON.parse(open(fs.readFileSync(path.join(DIR, "passes.json"))).toString("utf8")); } catch { PASSES = {}; }
    }
    console.log(`[unlock] ${ok.kind} ${ok.kind === "comp" ? PASSES[ok.nonce] || "label unknown (issued after deploy) " + ok.nonce : ok.nonce}`);
    ROWS_GZ ??= open(fs.readFileSync(path.join(DIR, "rows.json.gz")));
    res.setHeader("Content-Type", "application/json");
    res.setHeader("Content-Encoding", "gzip");
    return res.status(200).end(ROWS_GZ);
  }
  DETAIL ??= JSON.parse(zlib.gunzipSync(open(fs.readFileSync(path.join(DIR, "detail.json.gz")))));
  const d = DETAIL[String(id)];
  if (!d) return res.status(404).json({ error: "no such home" });
  res.status(200).json(d);
};
