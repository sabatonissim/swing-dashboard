// v29: the visitor's country, for the site's first-visit language (Israel -> Hebrew, anywhere else -> English).
// Vercel adds the x-vercel-ip-country header to every request (free, no account or key). Nothing is stored or logged.
// v71: /api/geo?choice=yes|no (the consent banner) also keeps that choice in a cookie for a year, and nothing else. A cookie set by
// the server stays: Safari deletes what a page's own script stored after 7 days without a visit, and the banner came back.
module.exports = (req, res) => {
  const country = String(req.headers['x-vercel-ip-country'] || '').toUpperCase().slice(0, 2) || null;
  res.setHeader('Cache-Control', 'private, no-store');
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  const choice = /[?&]choice=(yes|no)(?:&|$)/.exec(String(req.url || ''));
  if (choice) res.setHeader('Set-Cookie', 'swing_consent=' + choice[1] + '; Path=/; Max-Age=31536000; SameSite=Lax; Secure');
  res.statusCode = 200;
  res.end(JSON.stringify({ country }));
};
