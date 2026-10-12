// Shared by the paywall functions. Files starting with "_" are not routes on Vercel.
//
// The password is stateless: XXXX-XXXX-XXXX, where the first five characters are a
// random nonce minted when the Checkout Session is created and the last seven are an
// HMAC of that nonce under ACCESS_SECRET. A password we never issued fails the HMAC
// without a network call; one we issued only unlocks once Stripe says its session paid.
const crypto = require("crypto");

const SITE = process.env.SITE_URL || "https://msp-house-index.vercel.app";
const PRODUCT = "msp-house-index";
const ALPHA = "23456789BCDFGHJKMNPQRSTVWXYZ"; // no 0/O, 1/I/L, and no vowels A/E/U, so no password spells a word

function stripeKey() {
  const k = process.env.STRIPE_SECRET_KEY;
  if (!k) throw new Error("STRIPE_SECRET_KEY is not set");
  return k;
}

async function stripe(path, { method = "GET", form } = {}) {
  const r = await fetch(`https://api.stripe.com/v1/${path}`, {
    method,
    headers: { Authorization: `Bearer ${stripeKey()}`, ...(form ? { "Content-Type": "application/x-www-form-urlencoded" } : {}) },
    body: form ? new URLSearchParams(form).toString() : undefined,
  });
  const j = await r.json();
  if (!r.ok) throw new Error(`stripe ${path}: ${j.error?.message || r.status}`);
  return j;
}

const chars = (buf, n) => Array.from(buf.subarray(0, n), (b) => ALPHA[b % ALPHA.length]).join("");

function mac(nonce, domain = "") {
  const s = process.env.ACCESS_SECRET;
  if (!s) throw new Error("ACCESS_SECRET is not set");
  return chars(crypto.createHmac("sha256", s).update(domain + nonce).digest(), 7);
}

function newPassword() {
  const nonce = chars(crypto.randomBytes(5), 5);
  const raw = nonce + mac(nonce);
  return { nonce, password: `${raw.slice(0, 4)}-${raw.slice(4, 8)}-${raw.slice(8)}` };
}

const same = (a, b) => a.length === b.length && crypto.timingSafeEqual(Buffer.from(a), Buffer.from(b));

// { nonce, kind } if the HMAC holds, else null. Accepts any case, spaces or dashes.
// kind "paid": minted at checkout, unlocks once Stripe says its session paid.
// kind "comp": issued by hand (scripts/issue_password.py), unlocks with no session.
function checkPassword(pw) {
  const raw = String(pw || "").toUpperCase().replace(/[^0-9A-Z]/g, "");
  if (raw.length !== 12) return null;
  const nonce = raw.slice(0, 5), tail = raw.slice(5);
  if (same(tail, mac(nonce))) return { nonce, kind: "paid" };
  if (same(tail, mac(nonce, "comp:"))) return { nonce, kind: "comp" };
  return null;
}

const paid = new Map(); // nonce -> true, per warm instance
const LAUNCH = 1791700000; // 2026-10-11; no session of ours is older

// Paid, or a comp: a $0 session only the secret key can make (metadata.comp = "1").
const unlocked = (s) => s.metadata?.product === PRODUCT &&
  (s.payment_status === "paid" || (s.payment_status === "no_payment_required" && s.metadata?.comp === "1"));

// Is there a paid session for this nonce? Payment Intent search first (fast, but lags a
// minute and never sees comps), then every completed session since launch.
async function nonceIsPaid(nonce) {
  if (paid.get(nonce)) return true;
  try {
    const q = encodeURIComponent(`metadata['pw_nonce']:'${nonce}' AND status:'succeeded'`);
    const s = await stripe(`payment_intents/search?query=${q}&limit=1`);
    if (s.data?.length) return paid.set(nonce, true), true;
  } catch (e) { console.error("[pw] search failed:", e.message); }
  let after = "";
  for (let page = 0; page < 20; page++) {
    const l = await stripe(`checkout/sessions?status=complete&limit=100&created[gte]=${LAUNCH}${after}`);
    const hit = l.data.find((s) => s.metadata?.pw_nonce === nonce && unlocked(s));
    if (hit) return paid.set(nonce, true), true;
    if (!l.has_more) break;
    after = `&starting_after=${l.data[l.data.length - 1].id}`;
  }
  return false;
}

async function mailgun(fields) {
  const { MAILGUN_API_KEY, MAILGUN_DOMAIN } = process.env;
  if (!MAILGUN_API_KEY || !MAILGUN_DOMAIN) throw new Error("Mailgun is not configured");
  const r = await fetch(`https://api.mailgun.net/v3/${MAILGUN_DOMAIN}/messages`, {
    method: "POST",
    headers: { Authorization: "Basic " + Buffer.from("api:" + MAILGUN_API_KEY).toString("base64") },
    body: new URLSearchParams(fields),
  });
  const out = await r.text();
  if (!r.ok) throw new Error(`mailgun ${r.status}: ${out.slice(0, 200)}`);
  return JSON.parse(out).id || "";
}

// The password email. Sent once per session: the session's own metadata carries a
// pw_emailed marker, written before the send so a second caller backs off. Aidan gets
// a one-line sale notice alongside, so no sale goes unseen.
async function sendPasswordOnce(session, { who }) {
  if (!unlocked(session)) return { sent: false, why: "not ours or not paid" };
  const fresh = await stripe(`checkout/sessions/${session.id}`);
  if (fresh.metadata?.pw_emailed) return { sent: false, why: "already emailed " + fresh.metadata.pw_emailed };
  const mark = (v) => stripe(`checkout/sessions/${session.id}`, { method: "POST", form: { "metadata[pw_emailed]": v } });
  await mark(`${new Date().toISOString()} by ${who}`);
  const to = fresh.customer_details?.email;
  const pw = fresh.metadata.password;
  const link = `${SITE}/?pw=${encodeURIComponent(pw)}`;
  const from = process.env.MAILGUN_FROM || "aidan@brickandmortar.dev";
  const text = [
    `Thanks for buying the Twin Cities Under-Ask Index.`,
    ``,
    `Your password: ${pw}`,
    ``,
    `Open the full directory: ${link}`,
    ``,
    `That link unlocks it on whatever device you open it on. On another device, go to ${SITE} and enter the password under "Have a password?".`,
    ``,
    `Your receipt and invoice come separately from Stripe. Reply to this email with any question.`,
    ``,
    `Aidan`,
    `Brick & Mortar`,
  ].join("\n");
  const html = `<div style="font:16px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1a1a1a;max-width:520px">
<p>Thanks for buying the Twin Cities Under-Ask Index.</p>
<p>Your password:<br><b style="font:600 22px ui-monospace,Menlo,monospace;letter-spacing:2px">${pw}</b></p>
<p><a href="${link}" style="display:inline-block;padding:12px 20px;background:#1f4fd1;color:#fff;border-radius:8px;text-decoration:none;font-weight:600">Open the full directory</a></p>
<p>That link unlocks it on whatever device you open it on. On another device, go to <a href="${SITE}">${SITE.replace("https://", "")}</a> and enter the password under "Have a password?".</p>
<p>Your receipt and invoice come separately from Stripe. Reply to this email with any question.</p>
<p>Aidan<br>Brick &amp; Mortar</p></div>`;
  let id;
  try {
    id = await mailgun({ from: `Aidan at Brick & Mortar <${from}>`, to, subject: "Your password to the Twin Cities Under-Ask Index", text, html, "h:Reply-To": from });
  } catch (e) {
    await mark("").catch(() => {}); // let the other path (webhook retry, status page) try again
    throw e;
  }
  await stripe(`checkout/sessions/${session.id}`, { method: "POST", form: { "metadata[pw_mailgun_id]": id.slice(0, 400) } }).catch(() => {});
  console.log(`[pw] emailed ${to} for ${session.id} by ${who}`);
  await mailgun({
    from: `Under-Ask Index <${from}>`, to: process.env.NOTIFY_EMAIL || "aidan@brickandmortar.dev",
    subject: `Sale: Under-Ask Index, $${(fresh.amount_total || 0) / 100} from ${to}`,
    text: `${to} bought the full list (${fresh.payment_status}). Session ${fresh.id}. Password ${pw} was emailed to them by the ${who}.\nReprint it any time: python scripts/fulfil.py ${fresh.id}`,
  }).catch((e) => console.error("[pw] sale notice failed:", e.message));
  return { sent: true };
}

module.exports = { SITE, PRODUCT, stripe, newPassword, checkPassword, nonceIsPaid, sendPasswordOnce, unlocked };
