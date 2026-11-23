// v73: the analysts' rating history of up to 20 companies, read from Yahoo by this server, for analyst_ratings.py (GitHub Actions).
// Why: since 13.11.2026 Yahoo gives GitHub's computers a history that stops at 12.11, while other networks get the current one.
// Not used by the site. No key, no database, nothing stored. One module of one public page, only changes of the last 10 days.
// Load: 2 runs a day x 26 requests = ~1,100 a month (Vercel's free plan: 1,000,000).
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36';
const TICKER = /^[A-Z][A-Z0-9-]{0,9}$/;
const MAX_TICKERS = 20;
const KEEP_DAYS = 10;
const AT_ONCE = 5;
const UPSTREAM_MAX = 1200;          // requests to Yahoo in 10 minutes from one server (a whole run = ~510)
const FIELDS = ['epochGradeDate', 'firm', 'toGrade', 'fromGrade', 'action', 'priceTargetAction', 'currentPriceTarget', 'priorPriceTarget'];

let session = null;                 // { cookie, crumb, at }
let used = { since: 0, n: 0 };

function allow(n) {
  const now = Date.now();
  if (now - used.since > 600000) used = { since: now, n: 0 };
  if (used.n + n > UPSTREAM_MAX) return false;
  used.n += n;
  return true;
}

async function get(url, headers) {
  return fetch(url, { headers: { 'User-Agent': UA, ...headers }, signal: AbortSignal.timeout(8000) });
}

async function newSession() {
  const first = await get('https://fc.yahoo.com/', {});
  const raw = first.headers.getSetCookie ? first.headers.getSetCookie() : [first.headers.get('set-cookie') || ''];
  const cookie = raw.map((c) => c.split(';')[0]).filter(Boolean).join('; ');
  const r = await get('https://query1.finance.yahoo.com/v1/test/getcrumb', { Cookie: cookie });
  const crumb = r.ok ? (await r.text()).trim() : '';
  if (!crumb || crumb.length > 40 || /[<\s]/.test(crumb)) throw new Error('no session, HTTP ' + r.status);
  session = { cookie, crumb, at: Date.now() };
}

async function history(ticker) {
  const url = 'https://query2.finance.yahoo.com/v10/finance/quoteSummary/' + encodeURIComponent(ticker)
    + '?modules=upgradeDowngradeHistory&crumb=' + encodeURIComponent(session.crumb);
  const r = await get(url, { Cookie: session.cookie });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const d = await r.json();
  const all = d.quoteSummary.result[0].upgradeDowngradeHistory.history || [];
  all.sort((a, b) => b.epochGradeDate - a.epochGradeDate);
  const since = Date.now() / 1000 - KEEP_DAYS * 86400;
  // the newest one always: the reader tells a fresh answer from an old one by it
  return all.filter((x, i) => i === 0 || x.epochGradeDate >= since)
    .map((x) => Object.fromEntries(FIELDS.map((f) => [f, x[f] === undefined ? null : x[f]])));
}

function answer(res, status, body, cache) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', cache || 'no-store');
  res.setHeader('X-Robots-Tag', 'noindex');
  res.end(JSON.stringify(body));
}

module.exports = async (req, res) => {
  // only /api/ratings?t=A,B,C: another parameter would be a way around the stored copy
  const m = /^[^?]*\?t=([A-Z0-9,-]+)$/.exec(decodeURIComponent(String(req.url || '')));
  const tickers = m ? [...new Set(m[1].split(','))] : [];
  if (!tickers.length || tickers.length > MAX_TICKERS || !tickers.every((t) => TICKER.test(t))) return answer(res, 400, { error: 'bad request' });
  if (!allow(tickers.length + 2)) return answer(res, 429, { error: 'too many requests' });
  const results = {};
  const failed = {};
  try {
    if (!session || Date.now() - session.at > 1800000) await newSession();
  } catch (e) {
    return answer(res, 502, { error: String(e.message || e).slice(0, 80) });
  }
  for (let i = 0; i < tickers.length; i += AT_ONCE) {
    await Promise.all(tickers.slice(i, i + AT_ONCE).map(async (t) => {
      try { results[t] = await history(t); } catch (e) { failed[t] = String(e.message || e).slice(0, 80); }
    }));
  }
  if (Object.values(failed).some((x) => /HTTP 40[13]/.test(x))) session = null;      // an expired session: a new one next time
  const any = Object.keys(results).length > 0;
  return answer(res, any ? 200 : 502, { results, failed }, any && !Object.keys(failed).length ? 'public, s-maxage=600' : 'no-store');
};
module.exports._reset = () => { session = null; used = { since: 0, n: 0 }; };
