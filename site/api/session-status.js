// GET /api/session-status?session_id=cs_... -> { paid, email }
// Never returns the password: the email is the delivery. If the webhook has not
// emailed it yet, this sends it (after a short wait, so the two don't both send).
const { stripe, sendPasswordOnce, unlocked, PRODUCT } = require("./_lib");
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

module.exports = async (req, res) => {
  const id = String(req.query.session_id || "");
  if (!/^cs_(test|live)_[A-Za-z0-9]+$/.test(id)) return res.status(400).json({ error: "bad session id" });
  try {
    const s = await stripe(`checkout/sessions/${id}`);
    if (s.metadata?.product !== PRODUCT) return res.status(404).json({ error: "not found" });
    const paid = unlocked(s);
    let mail = null;
    if (paid) {
      if (!s.metadata?.pw_emailed) await wait(4000); // the webhook usually lands within a second or two
      try { mail = await sendPasswordOnce(s, { who: "status" }); } catch (e) { console.error("[status] mail:", e.message); mail = { sent: false, error: e.message.slice(0, 200) }; }
    }
    res.status(200).json({ paid, status: s.status, email: s.customer_details?.email || null, mailed: mail?.sent || /already/.test(mail?.why || ""), ...(req.query.debug && mail?.error ? { mail_error: mail.error } : {}) });
  } catch (e) {
    console.error("[status]", e.message);
    res.status(502).json({ error: "Could not check the payment. Refresh in a minute." });
  }
};
