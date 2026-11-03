// v29: the visitor's country, for the site's first-visit language (Israel -> Hebrew, anywhere else -> English).
// Vercel adds the x-vercel-ip-country header to every request (free, no account or key). Nothing is stored or logged.
module.exports = (req, res) => {
  const country = String(req.headers['x-vercel-ip-country'] || '').toUpperCase().slice(0, 2) || null;
  res.setHeader('Cache-Control', 'private, no-store');
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.statusCode = 200;
  res.end(JSON.stringify({ country }));
};
